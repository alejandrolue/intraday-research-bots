"""Lookahead-safe zone decision strategies for issue #56 research only."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from math import inf

from goldbot.models import Candle, MarketSetup
from goldbot.strategy import PullbackStrategy


ZONE_TOLERANCE_ATR = 0.25
ZONE_WIDTH_ATR = 0.25
PROBE_BUFFER_ATR = 0.10
BREAKOUT_BUFFER_ATR = 0.10
RETEST_TOLERANCE_ATR = 0.20
STOP_BUFFER_ATR = 0.25


@dataclass(frozen=True)
class Variant:
    family: str
    window: int
    radius: int
    parameter: float
    trend: str | None
    hours: str

    @property
    def id(self) -> str:
        suffix = "room" if self.family == "ema_room" else "rr"
        return (
            f"zone_{self.family}-w{self.window}-r{self.radius}-"
            f"{suffix}{self.parameter:g}-{self.trend or 'none'}-{self.hours}"
        )


@dataclass
class _Swing:
    kind: str
    price: float
    sequence: int
    timestamp: datetime
    broken_at: int | None = None


@dataclass
class _State:
    last_timestamp: datetime | None = None
    sequence: int = 0
    swings: list[_Swing] = field(default_factory=list)


@dataclass(frozen=True)
class PriceZone:
    kind: str
    low: float
    high: float
    touches: int


class ZoneDecisionStrategy:
    """Trade a confirmed zone reaction, or filter EMA signals with zone room."""

    def __init__(self, variant: Variant, *, base_strategy=None) -> None:
        if variant.family not in {"ema_room", "rejection", "breakout_retest"}:
            raise ValueError(f"Unknown zone family {variant.family!r}")
        self.variant = variant
        self.name = variant.id
        self.minimum_candles = max(variant.window + 1, 2 * variant.radius + 16)
        self._state = _State()
        self._base = base_strategy or PullbackStrategy()

    def analyze(self, candles: list[Candle]) -> MarketSetup:
        bar = candles[-1]
        waiting = MarketSetup("NO_TRADE", bar.timestamp, "No zone decision", "NEUTRAL", self.name)
        if len(candles) < self.minimum_candles:
            return waiting
        atr = self._atr(candles)
        if atr <= 0:
            return waiting
        self._sync(candles)
        intact = [swing for swing in self._state.swings if swing.broken_at is None]
        zones = self._zones(intact, atr)
        if self.variant.family == "ema_room":
            return self._ema_room(candles, zones, atr)
        if self.variant.family == "rejection":
            return self._rejection(candles[-2], bar, zones, atr) or waiting
        broken = [
            swing for swing in self._state.swings
            if swing.broken_at == self._state.sequence - 1
        ]
        return self._breakout_retest(candles[-2], bar, broken, zones, atr) or waiting

    @staticmethod
    def _atr(candles: list[Candle]) -> float:
        previous = candles[-16:-1]
        return sum(
            max(bar.high - bar.low, abs(bar.high - prior.close), abs(bar.low - prior.close))
            for prior, bar in zip(previous, previous[1:])
        ) / 14

    def _sync(self, candles: list[Candle]) -> None:
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
            for swing in state.swings:
                for index in range(swing.sequence + 1, len(candles) - 1):
                    if self._breaks(swing, candles[index].close):
                        swing.broken_at = index
                        break
        else:
            state.sequence += 1
            previous_sequence = state.sequence - 1
            for swing in state.swings:
                if swing.broken_at is None and self._breaks(swing, candles[-2].close):
                    swing.broken_at = previous_sequence
            self._maybe_add(
                candles,
                len(candles) - 1 - radius,
                state.sequence - radius,
            )
        state.last_timestamp = candles[-1].timestamp
        oldest = state.sequence - self.variant.window
        state.swings = [swing for swing in state.swings if swing.sequence >= oldest]

    def _maybe_add(self, candles: list[Candle], index: int, sequence: int) -> None:
        radius = self.variant.radius
        if index - radius < 0 or index + radius >= len(candles):
            return
        candle = candles[index]
        neighbors = candles[index - radius:index] + candles[index + 1:index + radius + 1]
        if candle.high > max(item.high for item in neighbors):
            self._state.swings.append(_Swing("supply", candle.high, sequence, candle.timestamp))
        if candle.low < min(item.low for item in neighbors):
            self._state.swings.append(_Swing("demand", candle.low, sequence, candle.timestamp))

    @staticmethod
    def _breaks(swing: _Swing, close: float) -> bool:
        return close > swing.price if swing.kind == "supply" else close < swing.price

    @staticmethod
    def _zones(swings: list[_Swing], atr: float) -> list[PriceZone]:
        zones = []
        tolerance = ZONE_TOLERANCE_ATR * atr
        width = ZONE_WIDTH_ATR * atr
        for kind in ("demand", "supply"):
            prices = sorted(swing.price for swing in swings if swing.kind == kind)
            clusters: list[list[float]] = []
            for price in prices:
                if clusters and price - clusters[-1][-1] <= tolerance:
                    clusters[-1].append(price)
                else:
                    clusters.append([price])
            for cluster in clusters:
                if kind == "demand":
                    low, high = min(cluster), max(cluster) + width
                else:
                    low, high = min(cluster) - width, max(cluster)
                zones.append(PriceZone(kind, low, high, len(cluster)))
        return zones

    @staticmethod
    def _room_r(direction: str, entry: float, risk: float, zones: list[PriceZone], atr: float) -> float:
        if risk <= 0:
            return -inf
        buffer = PROBE_BUFFER_ATR * atr
        if direction == "BUY":
            distances = [zone.low - entry for zone in zones if zone.kind == "supply" and zone.high > entry]
        else:
            distances = [entry - zone.high for zone in zones if zone.kind == "demand" and zone.low < entry]
        return (min(distances) - buffer) / risk if distances else inf

    def _ema_room(self, candles: list[Candle], zones: list[PriceZone], atr: float) -> MarketSetup:
        setup = self._base.analyze(candles)
        if not setup.ready:
            return replace(setup, strategy=self.name)
        assert setup.direction and setup.entry is not None and setup.stop is not None
        room = self._room_r(setup.direction, setup.entry, abs(setup.entry - setup.stop), zones, atr)
        if room < self.variant.parameter:
            return MarketSetup(
                "NO_TRADE", setup.timestamp,
                f"Opposing zone leaves only {room:.2f}R of room.", setup.bias, self.name,
                entry=setup.entry, stop=setup.stop, target=setup.target, atr=setup.atr,
                fast_ema=setup.fast_ema, slow_ema=setup.slow_ema,
            )
        return replace(
            setup,
            strategy=self.name,
            reason=f"{setup.reason} Opposing-zone room: {room:.2f}R.",
        )

    def _rejection(
        self, probe: Candle, bar: Candle, zones: list[PriceZone], atr: float
    ) -> MarketSetup | None:
        buffer = PROBE_BUFFER_ATR * atr
        longs = [
            zone for zone in zones
            if zone.kind == "demand" and probe.low <= zone.high + buffer
            and probe.close > zone.low and bar.close > probe.high and bar.close > bar.open
        ]
        shorts = [
            zone for zone in zones
            if zone.kind == "supply" and probe.high >= zone.low - buffer
            and probe.close < zone.high and bar.close < probe.low and bar.close < bar.open
        ]
        if bool(longs) == bool(shorts):
            return None
        direction = "BUY" if longs else "SELL"
        zone = max(longs, key=lambda item: item.high) if longs else min(shorts, key=lambda item: item.low)
        stop = (
            min(probe.low, zone.low) - STOP_BUFFER_ATR * atr
            if direction == "BUY"
            else max(probe.high, zone.high) + STOP_BUFFER_ATR * atr
        )
        return self._setup(bar, direction, stop, zones, atr, zone, "rejection")

    def _breakout_retest(
        self,
        breakout: Candle,
        bar: Candle,
        broken: list[_Swing],
        intact_zones: list[PriceZone],
        atr: float,
    ) -> MarketSetup | None:
        broken_zones = self._zones(broken, atr)
        breakout_buffer = BREAKOUT_BUFFER_ATR * atr
        retest = RETEST_TOLERANCE_ATR * atr
        longs = [
            zone for zone in broken_zones
            if zone.kind == "supply" and breakout.close > zone.high + breakout_buffer
            and bar.low <= zone.high + retest and bar.close > zone.high and bar.close > bar.open
        ]
        shorts = [
            zone for zone in broken_zones
            if zone.kind == "demand" and breakout.close < zone.low - breakout_buffer
            and bar.high >= zone.low - retest and bar.close < zone.low and bar.close < bar.open
        ]
        if bool(longs) == bool(shorts):
            return None
        direction = "BUY" if longs else "SELL"
        zone = max(longs, key=lambda item: item.high) if longs else min(shorts, key=lambda item: item.low)
        stop = (
            min(bar.low, zone.low) - STOP_BUFFER_ATR * atr
            if direction == "BUY"
            else max(bar.high, zone.high) + STOP_BUFFER_ATR * atr
        )
        return self._setup(bar, direction, stop, intact_zones, atr, zone, "breakout-retest")

    def _setup(
        self,
        bar: Candle,
        direction: str,
        stop: float,
        zones: list[PriceZone],
        atr: float,
        source: PriceZone,
        label: str,
    ) -> MarketSetup | None:
        risk = abs(bar.close - stop)
        if risk <= 0:
            return None
        room = self._room_r(direction, bar.close, risk, zones, atr)
        if room < self.variant.parameter:
            return None
        sign = 1.0 if direction == "BUY" else -1.0
        return MarketSetup(
            "READY_LONG" if direction == "BUY" else "READY_SHORT",
            bar.timestamp,
            f"Confirmed zone {label}; {room:.2f}R to the next opposing zone.",
            "BULLISH" if direction == "BUY" else "BEARISH",
            self.name,
            entry=bar.close,
            stop=stop,
            target=bar.close + sign * self.variant.parameter * risk,
            zone_low=source.low,
            zone_high=source.high,
            atr=atr,
        )
