"""Structure strategies: trade from one chart level to the next (issue #27).

Encodes the user's annotated gold setups:

- `zone`: price returns to a support (resistance) level that already held at least once, probes
  it and closes back on the right side with a rejection candle. Stop beyond the probe; target the
  nearest opposing swing level that still gives at least `min_rr`.
- `channel`: price closes through the trendline drawn from the last two higher swing lows (lower
  swing highs) after trending along it. Stop beyond the latest opposite swing; target the nearest
  swing level on the other side that still gives at least `min_rr`.

Swings are confirmed only `radius` candles after they print, so nothing uses future prices. The
strategy keeps an incremental swing memory; when it is called with a non-contiguous window (first
call, a gap, or a restart) it rebuilds from the window.

Strategy names: `structure_zone-w288-r3-rr1.5`, `structure_channel-w864-r6-rr2.5`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .models import Candle, MarketSetup


_NAME = re.compile(r"^structure_(zone|channel)-w(\d+)-r(\d+)-rr(\d+(?:\.\d+)?)$")
LEVEL_TOLERANCE_ATR = 0.25   # swings this close together form one level
PROBE_BUFFER_ATR = 0.10      # how far a probe (or a channel break) must reach past a level
STOP_BUFFER_ATR = 0.25       # stop distance beyond the structure


@dataclass(frozen=True)
class StructureVariant:
    family: str
    window: int
    radius: int
    min_rr: float

    @property
    def id(self) -> str:
        return f"structure_{self.family}-w{self.window}-r{self.radius}-rr{self.min_rr:g}"

    @classmethod
    def parse(cls, name: str) -> "StructureVariant":
        match = _NAME.match(name)
        if not match:
            raise ValueError(
                f"Unknown structure strategy {name!r}; expected e.g. structure_zone-w288-r3-rr1.5"
            )
        family, window, radius, rr = match.groups()
        return cls(family, int(window), int(radius), float(rr))


@dataclass
class _Swing:
    kind: str          # "high" or "low"
    price: float
    sequence: int      # candle sequence number of the swing candle
    timestamp: datetime
    broken: bool = False  # a later close went through it by the probe buffer


@dataclass
class _State:
    last_timestamp: datetime | None = None
    sequence: int = 0
    swings: list[_Swing] = field(default_factory=list)


class StructureStrategy:
    def __init__(self, variant: StructureVariant) -> None:
        self.variant = variant
        self.name = variant.id
        self.minimum_candles = 2 * variant.radius + 16
        self._state = _State()

    # ----- swing memory ---------------------------------------------------------------------

    def _sync(self, candles: list[Candle], atr: float) -> None:
        """Update swings, then mark levels broken by closes *before* the current candle."""
        state = self._state
        radius = self.variant.radius
        contiguous = (
            state.last_timestamp is not None
            and len(candles) >= 2
            and candles[-2].timestamp == state.last_timestamp
        )
        if not contiguous:
            state.swings = []
            state.sequence = len(candles) - 1
            for index in range(radius, len(candles) - radius):
                self._maybe_add(candles, index, index)
            positions = {candle.timestamp: offset for offset, candle in enumerate(candles)}
            for swing in state.swings:
                start = positions[swing.timestamp] + 1
                for candle in candles[start:-1]:
                    if self._breaks(swing, candle.close):
                        swing.broken = True
                        break
        else:
            state.sequence += 1
            # The previous candle is now "before"; a new swing cannot be broken by the candles
            # within its own radius, because its high/low bounds their closes.
            for swing in state.swings:
                if not swing.broken and self._breaks(swing, candles[-2].close):
                    swing.broken = True
            self._maybe_add(candles, len(candles) - 1 - radius, state.sequence - radius)
        state.last_timestamp = candles[-1].timestamp
        oldest = state.sequence - self.variant.window
        state.swings = [swing for swing in state.swings if swing.sequence >= oldest]

    def _maybe_add(self, candles: list[Candle], index: int, sequence: int) -> None:
        radius = self.variant.radius
        if index - radius < 0 or index + radius >= len(candles):
            return
        around = candles[index - radius : index + radius + 1]
        candle = candles[index]
        if candle.high >= max(item.high for item in around):
            self._state.swings.append(_Swing("high", candle.high, sequence, candle.timestamp))
        if candle.low <= min(item.low for item in around):
            self._state.swings.append(_Swing("low", candle.low, sequence, candle.timestamp))

    @staticmethod
    def _breaks(swing: _Swing, close: float) -> bool:
        # A level is broken by any close beyond it. No volatility buffer here, so the result is
        # identical whether the swing memory was built incrementally or rebuilt after a gap.
        return close > swing.price if swing.kind == "high" else close < swing.price

    # ----- analysis ---------------------------------------------------------------------------

    def analyze(self, candles: list[Candle]) -> MarketSetup:
        bar = candles[-1]
        waiting = MarketSetup("NO_TRADE", bar.timestamp, "No structure trigger", "NEUTRAL", self.name)
        if len(candles) < self.minimum_candles:
            return waiting
        previous = candles[-16:-1]
        atr = sum(
            max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
            for a, b in zip(previous, previous[1:])
        ) / 14
        self._sync(candles, atr)
        if atr <= 0:
            return waiting
        intact = [swing for swing in self._state.swings if not swing.broken]
        setup = (
            self._zone(bar, atr, intact) if self.variant.family == "zone"
            else self._channel(candles, atr, intact)
        )
        return setup or waiting

    def _target(self, direction: str, entry: float, stop: float, levels: list[_Swing]) -> float | None:
        risk = abs(entry - stop)
        if risk <= 0:
            return None
        if direction == "BUY":
            prices = sorted(s.price for s in levels if s.kind == "high" and s.price > entry)
            for price in prices:
                if price - entry >= self.variant.min_rr * risk:
                    return price
        else:
            prices = sorted((s.price for s in levels if s.kind == "low" and s.price < entry), reverse=True)
            for price in prices:
                if entry - price >= self.variant.min_rr * risk:
                    return price
        return None

    def _level(self, swings: list[_Swing], kind: str, around: float, atr: float) -> tuple[float, float] | None:
        tolerance = LEVEL_TOLERANCE_ATR * atr
        prices = [s.price for s in swings if s.kind == kind and abs(s.price - around) <= tolerance]
        if not prices:
            return None
        return min(prices), max(prices)

    def _zone(self, bar: Candle, atr: float, swings: list[_Swing]) -> MarketSetup | None:
        buffer = PROBE_BUFFER_ATR * atr
        # Long: the bar probes an intact support level and closes back above it, bullish.
        supports = [s for s in swings if s.kind == "low" and bar.low <= s.price + buffer and bar.close > s.price]
        resistances = [s for s in swings if s.kind == "high" and bar.high >= s.price - buffer and bar.close < s.price]
        candidates = []
        if supports and bar.close > bar.open:
            level = self._level(swings, "low", max(s.price for s in supports), atr)
            if level:
                stop = min(bar.low, level[0]) - STOP_BUFFER_ATR * atr
                candidates.append(("BUY", stop, level))
        if resistances and bar.close < bar.open:
            level = self._level(swings, "high", min(s.price for s in resistances), atr)
            if level:
                stop = max(bar.high, level[1]) + STOP_BUFFER_ATR * atr
                candidates.append(("SELL", stop, level))
        if len(candidates) != 1:
            return None
        direction, stop, level = candidates[0]
        target = self._target(direction, bar.close, stop, swings)
        if target is None:
            return None
        return self._setup(bar, direction, stop, target, atr, level,
                           f"Rejected {'support' if direction == 'BUY' else 'resistance'} "
                           f"{level[0]:.2f}-{level[1]:.2f}; target next level {target:.2f}.")

    def _channel(self, candles: list[Candle], atr: float, swings: list[_Swing]) -> MarketSetup | None:
        bar, prior = candles[-1], candles[-2]
        buffer = PROBE_BUFFER_ATR * atr
        now = self._state.sequence
        lows = [s for s in swings if s.kind == "low"]
        highs = [s for s in swings if s.kind == "high"]

        def line(a: _Swing, b: _Swing, sequence: int) -> float:
            slope = (b.price - a.price) / (b.sequence - a.sequence)
            return b.price + slope * (sequence - b.sequence)

        # Short: rising channel (last two swing lows higher) broken to the downside.
        if len(lows) >= 2 and highs:
            first, second = lows[-2], lows[-1]
            if second.price > first.price and second.sequence > first.sequence:
                level_now, level_before = line(first, second, now), line(first, second, now - 1)
                if prior.close >= level_before and bar.close < level_now - buffer and bar.close < bar.open:
                    stop = max(s.price for s in highs if s.sequence >= second.sequence) \
                        if any(s.sequence >= second.sequence for s in highs) else highs[-1].price
                    stop += STOP_BUFFER_ATR * atr
                    if stop > bar.close:
                        target = self._target("SELL", bar.close, stop, swings)
                        if target is not None:
                            return self._setup(bar, "SELL", stop, target, atr, (first.price, second.price),
                                               f"Rising channel broken; target {target:.2f}.")
        # Long: falling channel (last two swing highs lower) broken to the upside.
        if len(highs) >= 2 and lows:
            first, second = highs[-2], highs[-1]
            if second.price < first.price and second.sequence > first.sequence:
                level_now, level_before = line(first, second, now), line(first, second, now - 1)
                if prior.close <= level_before and bar.close > level_now + buffer and bar.close > bar.open:
                    recent = [s.price for s in lows if s.sequence >= second.sequence]
                    stop = (min(recent) if recent else lows[-1].price) - STOP_BUFFER_ATR * atr
                    if stop < bar.close:
                        target = self._target("BUY", bar.close, stop, swings)
                        if target is not None:
                            return self._setup(bar, "BUY", stop, target, atr, (second.price, first.price),
                                               f"Falling channel broken; target {target:.2f}.")
        return None

    def _setup(self, bar: Candle, direction: str, stop: float, target: float, atr: float,
               level: tuple[float, float], reason: str) -> MarketSetup:
        return MarketSetup(
            "READY_LONG" if direction == "BUY" else "READY_SHORT",
            bar.timestamp, reason, "BULLISH" if direction == "BUY" else "BEARISH", self.name,
            entry=bar.close, stop=stop, target=target, atr=atr,
            zone_low=min(level), zone_high=max(level),
        )
