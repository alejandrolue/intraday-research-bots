"""Issue #63: 5-minute GOLD EMA pullback with volume confirmation ("5m-v1").

Two entry families, both only in the direction of the 30-minute bias (`Ema21TrendFilter`):

- pullback: 5m EMA 9 above EMA 21, an impulse leg with above-average volume, a 1-6 candle pullback
  into the EMA 9/21 zone on below-average volume, then an engulfing or pin-bar trigger candle;
- box: an 8-candle quiet consolidation, a breakout with a volume spike, then either a retest with
  a trigger candle (A) or an entry at the breakout close itself (B).

Volume is Capital.com tick volume. It is compared either with its 20-candle average ("sma") or
with the same 5-minute time slot on the previous 3 trading days ("rvol"), or ignored ("off").

Shorts are evaluated by mirroring every price (high <-> -low), so long and short rules are the same
code. Every value is computed from completed candles only.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from .indicators import atr, ema
from .market_events import minutes_after_open, nearest_open, news_blackout
from .models import Candle, MarketSetup

ENTRY_TYPES = ("pullback", "retest", "breakout", "combined")
VOLUME_MODES = ("sma", "rvol", "off")
BAR = timedelta(minutes=5)
_NAME = re.compile(r"^evp-(pullback|retest|breakout|combined)-(sma|rvol|off)-rr(\d+(?:\.\d+)?)$")


@dataclass(frozen=True)
class VolumePullbackConfig:
    entry: str = "pullback"
    volume: str = "sma"
    reward_to_risk: float = 2.0
    fast: int = 9
    slow: int = 21
    atr_period: int = 14
    stop_atr: float = 2.0
    zone_buffer_atr: float = 0.1
    impulse_lookback: int = 12
    max_pullback: int = 6
    box_size: int = 8
    box_range_atr: float = 1.2
    breakout_buffer_atr: float = 0.1
    max_retest: int = 6
    rvol_days: int = 3
    impulse_threshold_sma: float = 1.0
    impulse_threshold_rvol: float = 1.5
    dryup_threshold: float = 1.0
    spike_threshold: float = 2.0
    reopen_guard_bars: int = 2  # no entry before 15 minutes after a break have passed
    spread_guard: float = 2.0
    open_spike_minutes: int = 15

    @property
    def name(self) -> str:
        return f"evp-{self.entry}-{self.volume}-rr{self.reward_to_risk:g}"

    @classmethod
    def parse(cls, name: str) -> "VolumePullbackConfig":
        match = _NAME.match(name)
        if not match:
            raise ValueError(f"Unknown volume pullback strategy {name!r}")
        return cls(entry=match.group(1), volume=match.group(2), reward_to_risk=float(match.group(3)))


def _mirror(candle: Candle) -> Candle:
    return Candle(candle.timestamp, -candle.open, -candle.low, -candle.high, -candle.close,
                  candle.volume, candle.spread)


def _ema_series(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    value = sum(values[:period]) / period
    out[period - 1] = value
    k = 2 / (period + 1)
    for index in range(period, len(values)):
        value = (values[index] - value) * k + value
        out[index] = value
    return out


def _engulfing(previous: Candle, candle: Candle) -> bool:
    return (candle.close > candle.open
            and max(candle.open, candle.close) >= max(previous.open, previous.close)
            and min(candle.open, candle.close) <= min(previous.open, previous.close))


def _pin_bar(candle: Candle) -> bool:
    body = abs(candle.close - candle.open)
    span = candle.high - candle.low
    lower_wick = min(candle.open, candle.close) - candle.low
    return (span > 0 and lower_wick >= 2 * body and lower_wick >= 0.5 * span
            and candle.close >= candle.low + 2 * span / 3)


def _trigger(previous: Candle, candle: Candle) -> bool:
    return _engulfing(previous, candle) or _pin_bar(candle)


class _Volume:
    """Volume of a candle relative to its baseline; None when no baseline exists."""

    def __init__(self, candles: list[Candle], mode: str, days: int) -> None:
        self.candles = candles
        self.mode = mode
        self.days = days
        self.by_time = {candle.timestamp: candle for candle in candles}

    def _slot_history(self, index: int) -> list[Candle]:
        found = []
        stamp = self.candles[index].timestamp
        for back in range(1, 8):
            prior = self.by_time.get(stamp - timedelta(days=back))
            if prior is not None:
                found.append(prior)
                if len(found) == self.days:
                    break
        return found

    def ratio(self, index: int) -> float | None:
        volume = self.candles[index].volume
        if self.mode == "sma":
            window = self.candles[max(0, index - 20):index]
            if len(window) < 20:
                return None
            base = sum(candle.volume for candle in window) / 20
        else:
            history = self._slot_history(index)
            if len(history) < self.days:
                return None
            base = sum(candle.volume for candle in history) / len(history)
        return volume / base if base > 0 else None

    def median_slot_spread(self, index: int) -> float | None:
        history = self._slot_history(index)
        return statistics.median(candle.spread for candle in history) if history else None


class VolumePullbackStrategy:
    def __init__(self, config: VolumePullbackConfig | None = None) -> None:
        self.config = config or VolumePullbackConfig()
        if self.config.entry not in ENTRY_TYPES or self.config.volume not in VOLUME_MODES:
            raise ValueError(f"Invalid volume pullback config {self.config}")
        self.name = self.config.name
        self.minimum_candles = 300

    # ----- filters on volume ratios -----------------------------------------------------------

    def _above(self, ratio: float | None, threshold: float) -> bool:
        return self.config.volume == "off" or (ratio is not None and ratio > threshold)

    def _below(self, ratio: float | None, threshold: float) -> bool:
        return self.config.volume == "off" or (ratio is not None and ratio < threshold)

    def _impulse_threshold(self) -> float:
        cfg = self.config
        return cfg.impulse_threshold_rvol if cfg.volume == "rvol" else cfg.impulse_threshold_sma

    # ----- entry -----------------------------------------------------------------------------

    def analyze(self, candles: list[Candle]) -> MarketSetup:
        ordered = sorted(candles, key=lambda candle: candle.timestamp)
        bar = ordered[-1]

        def skip(reason: str) -> MarketSetup:
            return MarketSetup("NO_TRADE", bar.timestamp, reason, "NEUTRAL", self.name)

        if len(ordered) < self.minimum_candles:
            return skip("Not enough candles")
        entry_time = bar.timestamp + BAR
        event = news_blackout(entry_time)
        if event:
            return skip(f"NEWS_BLACKOUT {event}")
        if self._after_break(ordered) < self.config.reopen_guard_bars:
            return skip("REOPEN_GUARD")
        volume = _Volume(ordered, self.config.volume, self.config.rvol_days)
        median_spread = volume.median_slot_spread(len(ordered) - 1)
        if median_spread and bar.spread > self.config.spread_guard * median_spread:
            return skip("SPREAD_GUARD")

        results = []
        for direction in ("BUY", "SELL"):
            view = ordered if direction == "BUY" else [_mirror(candle) for candle in ordered]
            found = self._long(view, volume)
            if isinstance(found, tuple):
                results.append((direction, *found))
            else:
                reason = found
        if len(results) != 1:
            return skip("Both directions triggered" if results else reason)
        direction, label, entry, stop = results[0]
        if direction == "SELL":
            entry, stop = -entry, -stop
        risk = abs(entry - stop)
        sign = 1.0 if direction == "BUY" else -1.0
        opened, minutes = nearest_open(entry_time)
        return MarketSetup(
            "READY_LONG" if direction == "BUY" else "READY_SHORT",
            bar.timestamp,
            f"{label}; nearest open {opened} {minutes:+d} min",
            "BULLISH" if direction == "BUY" else "BEARISH",
            self.name,
            entry=entry,
            stop=stop,
            target=entry + sign * self.config.reward_to_risk * risk,
            atr=atr(ordered, self.config.atr_period),
        )

    @staticmethod
    def _after_break(candles: list[Candle]) -> int:
        """Completed candles since the last gap of more than 30 minutes (a break or reopen)."""
        for back in range(1, min(len(candles), 12)):
            if candles[-back].timestamp - candles[-back - 1].timestamp > timedelta(minutes=30):
                return back - 1
        return 99

    def _long(self, candles: list[Candle], volume: _Volume) -> tuple[str, float, float] | str:
        cfg = self.config
        closes = [candle.close for candle in candles[-300:]]
        offset = len(candles) - len(closes)
        fast_series = _ema_series(closes, cfg.fast)
        slow_series = _ema_series(closes, cfg.slow)

        def fast(index: int) -> float:
            return fast_series[index - offset]  # type: ignore[return-value]

        def slow(index: int) -> float:
            return slow_series[index - offset]  # type: ignore[return-value]

        current_atr = atr(candles, cfg.atr_period)
        if current_atr <= 0:
            return "NO_ATR"
        reasons = []
        if cfg.entry in ("pullback", "combined"):
            found = self._pullback(candles, volume, fast, slow, current_atr)
            if isinstance(found, tuple):
                return found
            reasons.append(found)
        if cfg.entry in ("retest", "combined"):
            found = self._box_retest(candles, volume, fast, slow, current_atr)
            if isinstance(found, tuple):
                return found
            reasons.append(found)
        if cfg.entry == "breakout":
            found = self._box_breakout(candles, volume, current_atr)
            if isinstance(found, tuple):
                return found
            reasons.append(found)
        return reasons[0]

    def _pullback(self, candles, volume, fast, slow, current_atr):
        cfg = self.config
        last = len(candles) - 1
        trigger = candles[last]
        if not fast(last) > slow(last):
            return "NO_ALIGNMENT"
        # The swing high that the pullback started from: 1-6 pullback candles before the trigger.
        for length in range(1, cfg.max_pullback + 1):
            swing = last - length - 1
            pullback = range(swing + 1, last)
            if not all(candles[i].high <= candles[swing].high for i in range(swing + 1, last + 1)):
                continue
            if candles[swing].high < max(c.high for c in candles[swing - cfg.impulse_lookback:swing]):
                continue
            if not all(fast(i) > slow(i) for i in range(swing + 1, last + 1)):
                return "NO_ALIGNMENT"
            leg = range(swing - cfg.impulse_lookback + 1, swing + 1)
            if not any(candles[i].close > candles[i].open
                       and self._above(volume.ratio(i), self._impulse_threshold()) for i in leg):
                return "NO_IMPULSE"
            touched = range(swing + 1, last + 1)
            if any(candles[i].close < slow(i) for i in touched):
                return "NO_PULLBACK_ZONE"
            if not any(slow(i) - cfg.zone_buffer_atr * current_atr <= candles[i].low <= fast(i)
                       for i in touched):
                return "NO_PULLBACK_ZONE"
            if not all(self._below(volume.ratio(i), cfg.dryup_threshold) for i in pullback):
                return "NO_DRYUP"
            if not (trigger.close > fast(last) and _trigger(candles[last - 1], trigger)):
                return "NO_TRIGGER"
            entry = trigger.close
            stop = entry - cfg.stop_atr * current_atr
            pullback_low = min(candles[i].low for i in touched)
            if pullback_low - cfg.zone_buffer_atr * current_atr < stop:
                return "STOP_TOO_WIDE"
            return ("EMA pullback with volume dry-up", entry, stop)
        return "NO_PULLBACK"

    def _box(self, candles, volume, breakout: int):
        """The quiet 8-candle box before `breakout`, or None."""
        cfg = self.config
        first = breakout - cfg.box_size
        if first - cfg.atr_period - 1 < 0:
            return None
        before_atr = atr(candles[first - cfg.atr_period - 1:first + 1], cfg.atr_period)
        box = candles[first:breakout]
        high, low = max(c.high for c in box), min(c.low for c in box)
        if before_atr <= 0 or high - low > cfg.box_range_atr * before_atr:
            return None
        if cfg.volume != "off":
            ratios = [volume.ratio(i) for i in range(first, breakout)]
            if any(r is None for r in ratios) or sum(ratios) / len(ratios) >= cfg.dryup_threshold:
                return None
        candle = candles[breakout]
        if not (candle.close > high + cfg.breakout_buffer_atr * before_atr and candle.close > candle.open):
            return None
        if not self._above(volume.ratio(breakout), cfg.spike_threshold - 1e-9):
            return None
        return high, low, before_atr

    def _box_breakout(self, candles, volume, current_atr):
        cfg = self.config
        last = len(candles) - 1
        box = self._box(candles, volume, last)
        if box is None:
            return "NO_BOX_BREAKOUT"
        after_open = minutes_after_open(candles[last].timestamp + BAR)
        if after_open is not None and after_open < cfg.open_spike_minutes:
            return "OPEN_SPIKE"
        high, low, before_atr = box
        entry = candles[last].close
        stop = low - cfg.breakout_buffer_atr * before_atr
        if entry - stop > cfg.stop_atr * current_atr:
            return "STOP_TOO_WIDE"
        return ("Box breakout close", entry, stop)

    def _box_retest(self, candles, volume, fast, slow, current_atr):
        cfg = self.config
        last = len(candles) - 1
        for breakout in range(last - 1, last - cfg.max_retest - 1, -1):
            box = self._box(candles, volume, breakout)
            if box is None:
                continue
            high, _, before_atr = box
            after = range(breakout + 1, last + 1)
            if any(candles[i].close <= high for i in after):
                return "BOX_FAILED"
            if not any(candles[i].low <= high + cfg.breakout_buffer_atr * before_atr
                       or candles[i].low <= fast(i) for i in after):
                return "NO_RETEST"
            if not all(self._below(volume.ratio(i), cfg.dryup_threshold)
                       for i in range(breakout + 1, last)):
                return "NO_DRYUP"
            if not _trigger(candles[last - 1], candles[last]):
                return "NO_TRIGGER"
            entry = candles[last].close
            stop = min(candles[i].low for i in after) - cfg.breakout_buffer_atr * before_atr
            if entry - stop > cfg.stop_atr * current_atr or entry <= stop:
                return "STOP_TOO_WIDE"
            return ("Box breakout retest", entry, stop)
        return "NO_BOX_BREAKOUT"


class Ema21TrendFilter:
    """Issue #63 30-minute bias: two closes beyond EMA 21 by a margin, and away from EMA 200."""

    name = "ema21_spec"
    minimum_candles = 215

    def analyze(self, candles: list[Candle]) -> Literal["BULLISH", "BEARISH", "NEUTRAL"]:
        ordered = sorted(candles, key=lambda candle: candle.timestamp)
        closes = [candle.close for candle in ordered]
        ema21 = _ema_series(closes, 21)
        ema200 = ema(closes, 200)
        band = atr(ordered, 14)
        last, previous = closes[-1], closes[-2]
        if abs(last - ema200) < 0.5 * band:
            return "NEUTRAL"
        if previous > ema21[-2] and last > ema21[-1] and last - ema21[-1] >= 0.25 * band:
            return "BULLISH"
        if previous < ema21[-2] and last < ema21[-1] and ema21[-1] - last >= 0.25 * band:
            return "BEARISH"
        return "NEUTRAL"
