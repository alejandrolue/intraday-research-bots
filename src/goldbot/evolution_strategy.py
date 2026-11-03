"""Zone-aware evolution of the original 5-minute Gold strategy set.

The strategy has two symmetric entry paths:

- confluence: an original 5m-learn signal must react from an intact same-side zone and have
  enough room to the nearest opposing zone;
- fakeout: a confirmed zone break is reclaimed and confirmed in the opposite direction.

Swing levels become visible only after ``radius`` later candles, so neither path uses future data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import inf

from .indicators import atr
from .models import Candle, MarketSetup
from .strategy import DemandZoneStrategy, PullbackStrategy, RetestStrategy


@dataclass(frozen=True)
class EvolutionConfig:
    window: int = 288
    radius: int = 6
    zone_tolerance_atr: float = 0.25
    zone_width_atr: float = 0.25
    interaction_atr: float = 0.10
    stop_buffer_atr: float = 0.25
    reward_to_risk: float = 2.0


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
class _Zone:
    kind: str
    low: float
    high: float
    anchors: int


class FiveMinuteEvolutionStrategy:
    name = "5m_evolution"

    def __init__(self, config: EvolutionConfig | None = None) -> None:
        self.config = config or EvolutionConfig()
        self.minimum_candles = max(
            self.config.window + 1,
            2 * self.config.radius + 16,
            DemandZoneStrategy().minimum_candles,
        )
        self._state = _State()
        self._original = (RetestStrategy(), PullbackStrategy(), DemandZoneStrategy())

    def analyze(self, candles: list[Candle]) -> MarketSetup:
        ordered = sorted(candles, key=lambda candle: candle.timestamp)
        bar = ordered[-1]
        waiting = MarketSetup(
            "NO_TRADE", bar.timestamp, "No 5m evolution trigger.", "NEUTRAL", self.name
        )
        if len(ordered) < self.minimum_candles:
            return waiting
        current_atr = atr(ordered[:-1], 14)
        if current_atr <= 0:
            return waiting
        self._sync(ordered)
        intact_swings = [swing for swing in self._state.swings if swing.broken_at is None]
        intact_zones = self._zones(intact_swings, current_atr)
        recently_broken = [
            swing
            for swing in self._state.swings
            if swing.broken_at == self._state.sequence - 1
        ]

        fakeout = self._fakeout(
            ordered[-2],
            bar,
            intact_zones,
            self._zones(recently_broken, current_atr),
            current_atr,
        )
        confluence = self._confluence(ordered, intact_zones, current_atr)
        if fakeout and confluence and fakeout.direction != confluence.direction:
            return MarketSetup(
                "NO_TRADE",
                bar.timestamp,
                "Confluence and fakeout paths disagreed; entry blocked.",
                "NEUTRAL",
                self.name,
                atr=current_atr,
            )
        return fakeout or confluence or waiting

    def _sync(self, candles: list[Candle]) -> None:
        state = self._state
        radius = self.config.radius
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
        oldest = state.sequence - self.config.window
        state.swings = [swing for swing in state.swings if swing.sequence >= oldest]

    def _maybe_add(self, candles: list[Candle], index: int, sequence: int) -> None:
        radius = self.config.radius
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

    def _zones(self, swings: list[_Swing], current_atr: float) -> list[_Zone]:
        zones = []
        tolerance = self.config.zone_tolerance_atr * current_atr
        width = self.config.zone_width_atr * current_atr
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
                zones.append(_Zone(kind, low, high, len(cluster)))
        return zones

    def _base_setup(self, candles: list[Candle]) -> MarketSetup | None:
        ready = [strategy.analyze(candles) for strategy in self._original]
        ready = [setup for setup in ready if setup.ready]
        if not ready or len({setup.direction for setup in ready}) != 1:
            return None
        return ready[0]

    def _confluence(
        self, candles: list[Candle], zones: list[_Zone], current_atr: float
    ) -> MarketSetup | None:
        base = self._base_setup(candles)
        if base is None or base.entry is None or base.stop is None or base.direction is None:
            return None
        probe, bar = candles[-2], candles[-1]
        tolerance = self.config.interaction_atr * current_atr
        if base.direction == "BUY":
            supporting = [
                zone
                for zone in zones
                if zone.kind == "demand"
                and min(probe.low, bar.low) <= zone.high + tolerance
                and bar.close > zone.high
            ]
            if not supporting:
                return None
            source = max(supporting, key=lambda zone: zone.high)
            stop = min(base.stop, probe.low, source.low) - self.config.stop_buffer_atr * current_atr
        else:
            supporting = [
                zone
                for zone in zones
                if zone.kind == "supply"
                and max(probe.high, bar.high) >= zone.low - tolerance
                and bar.close < zone.low
            ]
            if not supporting:
                return None
            source = min(supporting, key=lambda zone: zone.low)
            stop = max(base.stop, probe.high, source.high) + self.config.stop_buffer_atr * current_atr
        return self._setup(
            bar,
            base.direction,
            stop,
            zones,
            source,
            current_atr,
            "confluence",
            f"{base.strategy} confirmed at an intact zone",
        )

    def _fakeout(
        self,
        failed_break: Candle,
        bar: Candle,
        intact: list[_Zone],
        recently_broken: list[_Zone],
        current_atr: float,
    ) -> MarketSetup | None:
        buffer = self.config.interaction_atr * current_atr
        long_zones = [
            zone
            for zone in intact
            if zone.kind == "demand"
            and failed_break.low < zone.low - buffer
            and failed_break.close > zone.high
            and bar.close > failed_break.high
            and bar.close > bar.open
        ]
        long_zones += [
            zone
            for zone in recently_broken
            if zone.kind == "demand"
            and failed_break.close < zone.low - buffer
            and bar.close > max(zone.high, failed_break.high)
            and bar.close > bar.open
        ]
        short_zones = [
            zone
            for zone in intact
            if zone.kind == "supply"
            and failed_break.high > zone.high + buffer
            and failed_break.close < zone.low
            and bar.close < failed_break.low
            and bar.close < bar.open
        ]
        short_zones += [
            zone
            for zone in recently_broken
            if zone.kind == "supply"
            and failed_break.close > zone.high + buffer
            and bar.close < min(zone.low, failed_break.low)
            and bar.close < bar.open
        ]
        if bool(long_zones) == bool(short_zones):
            return None
        if long_zones:
            source = max(long_zones, key=lambda zone: zone.high)
            stop = min(failed_break.low, source.low) - self.config.stop_buffer_atr * current_atr
            return self._setup(
                bar, "BUY", stop, intact, source, current_atr, "fakeout", "Failed demand break reclaimed"
            )
        source = min(short_zones, key=lambda zone: zone.low)
        stop = max(failed_break.high, source.high) + self.config.stop_buffer_atr * current_atr
        return self._setup(
            bar, "SELL", stop, intact, source, current_atr, "fakeout", "Failed supply break reclaimed"
        )

    def _room_r(
        self, direction: str, entry: float, risk: float, zones: list[_Zone], current_atr: float
    ) -> float:
        if risk <= 0:
            return -inf
        buffer = self.config.interaction_atr * current_atr
        if direction == "BUY":
            distances = [
                zone.low - entry
                for zone in zones
                if zone.kind == "supply" and zone.high > entry
            ]
        else:
            distances = [
                entry - zone.high
                for zone in zones
                if zone.kind == "demand" and zone.low < entry
            ]
        return (min(distances) - buffer) / risk if distances else inf

    def _setup(
        self,
        bar: Candle,
        direction: str,
        stop: float,
        zones: list[_Zone],
        source: _Zone,
        current_atr: float,
        mode: str,
        reason: str,
    ) -> MarketSetup | None:
        risk = abs(bar.close - stop)
        room = self._room_r(direction, bar.close, risk, zones, current_atr)
        if risk <= 0 or room < self.config.reward_to_risk:
            return None
        sign = 1 if direction == "BUY" else -1
        return MarketSetup(
            "READY_LONG" if direction == "BUY" else "READY_SHORT",
            bar.timestamp,
            f"{reason}; {room:.2f}R to the next opposing zone.",
            "BULLISH" if direction == "BUY" else "BEARISH",
            f"5m_evolution_{mode}",
            entry=bar.close,
            stop=stop,
            target=bar.close + sign * self.config.reward_to_risk * risk,
            zone_low=source.low,
            zone_high=source.high,
            atr=current_atr,
        )


class DailyEvolutionStrategy(FiveMinuteEvolutionStrategy):
    """The #60 locked diagnostic: the #58 rules with a 144-bar window and radius-3 swings.

    It trades about daily across four markets but failed its study (-0.109R/trade on the holdout);
    it runs only as a demo execution experiment.
    """

    name = "5m_evolution_daily"

    def __init__(self, config: EvolutionConfig | None = None) -> None:
        super().__init__(config or EvolutionConfig(window=144, radius=3))
