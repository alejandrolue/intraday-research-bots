"""ICT Silver Bullet and ICT Turtle Soup, as fixed in research/ICT_PROTOCOL.md (issue #40).

Both are written for longs; shorts use the same code on mirrored prices (every price negated,
highs and lows swapped), so the two directions follow identical rules.

Names: `silverbullet-am-hour-fvg-rr2` (window, liquidity, stop, target) and
`turtlesoup-dayh1-r2-rr1.5` (levels, structure radius, minimum reward-to-risk).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import Candle, MarketSetup


NEW_YORK = ZoneInfo("America/New_York")
BUFFER_ATR = 0.10
WINDOWS = {"london": time(3, 0), "am": time(10, 0), "pm": time(14, 0)}


def _atr(candles: list[Candle]) -> float:
    previous = candles[-16:-1]
    return sum(
        max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
        for a, b in zip(previous, previous[1:])
    ) / 14


@dataclass(frozen=True)
class _Bars:
    """A price series seen from one direction; `sign` -1 mirrors it for shorts."""

    lows: list[float]
    highs: list[float]
    closes: list[float]

    @classmethod
    def of(cls, candles: list[Candle], sign: int) -> "_Bars":
        if sign > 0:
            return cls([c.low for c in candles], [c.high for c in candles], [c.close for c in candles])
        return cls([-c.high for c in candles], [-c.low for c in candles], [-c.close for c in candles])


def _setup(bar: Candle, name: str, sign: int, entry: float, stop: float, target: float,
           atr: float, reason: str) -> MarketSetup:
    long = sign > 0
    return MarketSetup(
        "READY_LONG" if long else "READY_SHORT", bar.timestamp, reason,
        "BULLISH" if long else "BEARISH", name,
        entry=entry * sign, stop=stop * sign, target=target * sign, atr=atr,
    )


# ----- Silver Bullet ----------------------------------------------------------------------------

_SB = re.compile(r"^silverbullet-(london|am|pm)-(hour|day)-(fvg|sweep)-(rr2|liq)$")


@dataclass(frozen=True)
class SilverBulletVariant:
    window: str
    liquidity: str
    stop: str
    target: str

    @property
    def id(self) -> str:
        return f"silverbullet-{self.window}-{self.liquidity}-{self.stop}-{self.target}"

    @classmethod
    def parse(cls, name: str) -> "SilverBulletVariant":
        match = _SB.match(name)
        if not match:
            raise ValueError(f"Unknown Silver Bullet strategy {name!r}")
        return cls(*match.groups())


class SilverBulletStrategy:
    minimum_candles = 16

    def __init__(self, variant: SilverBulletVariant, *, bar_minutes: int = 5,
                 start: datetime | None = None) -> None:
        self.variant = variant
        self.name = variant.id
        self.bar_minutes = bar_minutes
        self.start = start

    def analyze(self, candles: list[Candle]) -> MarketSetup:
        bar = candles[-1]
        waiting = MarketSetup("NO_TRADE", bar.timestamp, "No Silver Bullet setup", "NEUTRAL", self.name)
        if len(candles) < self.minimum_candles or (self.start and bar.timestamp < self.start):
            return waiting
        local = bar.timestamp.astimezone(NEW_YORK)
        if local.weekday() >= 5:
            return waiting
        opens = local.replace(hour=WINDOWS[self.variant.window].hour, minute=0, second=0, microsecond=0)
        closes = opens + timedelta(minutes=60)
        if not (opens <= local and local + timedelta(minutes=self.bar_minutes) <= closes):
            return waiting
        window_start = opens.astimezone(timezone.utc)
        liquidity_start = (
            window_start - timedelta(minutes=60) if self.variant.liquidity == "hour"
            else opens.replace(hour=0).astimezone(timezone.utc)
        )
        index = len(candles) - 1
        while index > 0 and candles[index - 1].timestamp >= liquidity_start:
            index -= 1
        tail = candles[index:]
        window = [c for c in tail if c.timestamp >= window_start]
        before = [c for c in tail if c.timestamp < window_start]
        if not before or not window:
            return waiting
        atr = _atr(candles)
        if atr <= 0:
            return waiting
        found = []
        for sign in (1, -1):
            result = self._long(_Bars.of(window, sign), _Bars.of(before, sign), atr)
            if result:
                found.append((sign, *result))
        if len(found) != 1:
            return waiting
        sign, entry, stop, target = found[0]
        return _setup(bar, self.name, sign, entry, stop, target, atr,
                      f"Silver Bullet {self.variant.window}: sweep, FVG, retrace entry.")

    def _long(self, w: _Bars, liq: _Bars, atr: float) -> tuple[float, float, float] | None:
        low_level, high_level = min(liq.lows), max(liq.highs)
        last = len(w.closes) - 1
        sweep = next((i for i in range(last + 1) if w.lows[i] < low_level), None)
        if sweep is None or not any(w.closes[k] > low_level for k in range(sweep, last + 1)):
            return None
        fvg = None
        for third in range(last - 1, sweep, -1):
            if third >= 2 and w.highs[third - 2] < w.lows[third]:
                fvg = third
                break
        if fvg is None:
            return None
        bottom, top = w.highs[fvg - 2], w.lows[fvg]
        touch = next((j for j in range(fvg + 1, last + 1) if w.lows[j] <= top), None)
        if touch != last or w.closes[last] < bottom:
            return None
        entry = w.closes[last]
        extreme = min(w.lows[: last + 1])
        stop = (bottom if self.variant.stop == "fvg" else extreme) - BUFFER_ATR * atr
        if stop >= entry:
            return None
        risk = entry - stop
        target = entry + 2 * risk if self.variant.target == "rr2" else high_level
        if target - entry < risk:
            return None
        return entry, stop, target


# ----- Turtle Soup ------------------------------------------------------------------------------

_TS = re.compile(r"^turtlesoup-(day|dayh1)-r(\d+)-rr(\d+(?:\.\d+)?)$")
SWEEP_LOOKBACK = 12
STRUCTURE_LOOKBACK = 60
H1_RADIUS = 2
H1_DAYS = 5


@dataclass(frozen=True)
class TurtleSoupVariant:
    levels: str
    radius: int
    min_rr: float

    @property
    def id(self) -> str:
        return f"turtlesoup-{self.levels}-r{self.radius}-rr{self.min_rr:g}"

    @classmethod
    def parse(cls, name: str) -> "TurtleSoupVariant":
        match = _TS.match(name)
        if not match:
            raise ValueError(f"Unknown Turtle Soup strategy {name!r}")
        levels, radius, rr = match.groups()
        return cls(levels, int(radius), float(rr))


class TurtleSoupStrategy:
    def __init__(self, variant: TurtleSoupVariant, *, start: datetime | None = None) -> None:
        self.variant = variant
        self.name = variant.id
        self.start = start
        self.minimum_candles = 16 + SWEEP_LOOKBACK + STRUCTURE_LOOKBACK
        self._days: dict[date, tuple[float, float]] = {}
        self._hour_key: datetime | None = None
        self._h1: tuple[list[float], list[float]] = ([], [])

    def analyze(self, candles: list[Candle]) -> MarketSetup:
        bar = candles[-1]
        waiting = MarketSetup("NO_TRADE", bar.timestamp, "No Turtle Soup setup", "NEUTRAL", self.name)
        if len(candles) < self.minimum_candles or (self.start and bar.timestamp < self.start):
            return waiting
        atr = _atr(candles)
        if atr <= 0:
            return waiting
        previous = self._previous_day(candles)
        if previous is None:
            return waiting
        pdh, pdl = previous
        highs, lows = [pdh], [pdl]
        if self.variant.levels == "dayh1":
            swing_highs, swing_lows = self._hourly_swings(candles)
            highs += swing_highs
            lows += swing_lows
        tail = candles[-(SWEEP_LOOKBACK + STRUCTURE_LOOKBACK + 1):]
        found = []
        for sign in (1, -1):
            ssl = lows if sign > 0 else [-h for h in highs]
            bsl = highs if sign > 0 else [-l for l in lows]
            result = self._long(_Bars.of(tail, sign), ssl, bsl, atr)
            if result:
                found.append((sign, *result))
        if len(found) != 1:
            return waiting
        sign, entry, stop, target = found[0]
        return _setup(bar, self.name, sign, entry, stop, target, atr,
                      "Turtle Soup: HTF liquidity swept, structure shifted.")

    def _long(self, t: _Bars, ssl: list[float], bsl: list[float], atr: float) -> tuple[float, float, float] | None:
        last = len(t.closes) - 1
        radius = self.variant.radius
        sweep, level = None, None
        for s in range(last - 1, max(last - 1 - SWEEP_LOOKBACK, 0) - 1, -1):
            swept = [value for value in ssl if t.lows[s] < value]
            if swept:
                candidate = min(swept)
                if any(t.closes[k] > candidate for k in range(s, last + 1)):
                    sweep, level = s, candidate
                    break
        if sweep is None:
            return None
        structure = None
        for i in range(sweep - 1, max(sweep - STRUCTURE_LOOKBACK, radius) - 1, -1):
            if i + radius > last - 1:
                continue
            if t.highs[i] >= max(t.highs[i - radius : i + radius + 1]):
                structure = t.highs[i]
                break
        if structure is None:
            return None
        if not t.closes[last] > structure or any(t.closes[k] > structure for k in range(sweep, last)):
            return None
        entry = t.closes[last]
        stop = min(t.lows[sweep : last + 1]) - BUFFER_ATR * atr
        if stop >= entry:
            return None
        risk = entry - stop
        for target in sorted(value for value in bsl if value > entry):
            if target - entry >= self.variant.min_rr * risk:
                return entry, stop, target
        return None

    def _previous_day(self, candles: list[Candle]) -> tuple[float, float] | None:
        today = candles[-1].timestamp.astimezone(NEW_YORK).date()
        if today in self._days:
            return self._days[today]
        index = len(candles) - 1
        while index >= 0 and candles[index].timestamp.astimezone(NEW_YORK).date() == today:
            index -= 1
        if index < 0:
            return None
        day = candles[index].timestamp.astimezone(NEW_YORK).date()
        block = []
        while index >= 0 and candles[index].timestamp.astimezone(NEW_YORK).date() == day:
            block.append(candles[index])
            index -= 1
        if index < 0:  # the previous day may be cut off by the start of the window
            return None
        self._days[today] = (max(c.high for c in block), min(c.low for c in block))
        return self._days[today]

    def _hourly_swings(self, candles: list[Candle]) -> tuple[list[float], list[float]]:
        current = candles[-1].timestamp.replace(minute=0, second=0, microsecond=0)
        if current == self._hour_key:
            return self._h1
        horizon = current - timedelta(days=H1_DAYS)
        hours: dict[datetime, list[float]] = {}
        for candle in candles:
            key = candle.timestamp.replace(minute=0, second=0, microsecond=0)
            if key < horizon or key >= current:
                continue  # only completed hours within the horizon
            if key in hours:
                hours[key][0] = max(hours[key][0], candle.high)
                hours[key][1] = min(hours[key][1], candle.low)
            else:
                hours[key] = [candle.high, candle.low]
        keys = sorted(hours)
        highs, lows = [], []
        for i in range(H1_RADIUS, len(keys) - H1_RADIUS):
            window = keys[i - H1_RADIUS : i + H1_RADIUS + 1]
            if hours[keys[i]][0] >= max(hours[k][0] for k in window):
                highs.append(hours[keys[i]][0])
            if hours[keys[i]][1] <= min(hours[k][1] for k in window):
                lows.append(hours[keys[i]][1])
        self._hour_key, self._h1 = current, (highs, lows)
        return self._h1
