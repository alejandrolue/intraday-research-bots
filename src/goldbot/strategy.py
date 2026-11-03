from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .indicators import atr, ema
from .models import Candle, MarketSetup


@dataclass(frozen=True)
class StrategyConfig:
    fast_ema_period: int = 20
    slow_ema_period: int = 50
    atr_period: int = 14
    range_lookback: int = 20
    breakout_buffer_atr: float = 0.10
    retest_tolerance_atr: float = 0.20
    stop_buffer_atr: float = 0.25
    reward_to_risk: float = 2.0


class RetestStrategy:
    name = "breakout_retest"

    def __init__(self, config: StrategyConfig | None = None) -> None:
        self.config = config or StrategyConfig()

    @property
    def minimum_candles(self) -> int:
        cfg = self.config
        return max(cfg.slow_ema_period, cfg.atr_period + 1, cfg.range_lookback + 2)

    def analyze(self, candles: list[Candle]) -> MarketSetup:
        if len(candles) < self.minimum_candles:
            raise ValueError(
                f"Strategy requires at least {self.minimum_candles} completed candles; "
                f"received {len(candles)}"
            )

        ordered = sorted(candles, key=lambda candle: candle.timestamp)
        cfg = self.config
        closes = [candle.close for candle in ordered]
        fast = ema(closes, cfg.fast_ema_period)
        slow = ema(closes, cfg.slow_ema_period)
        current_atr = atr(ordered, cfg.atr_period)
        breakout = ordered[-2]
        confirmation = ordered[-1]
        base = ordered[-(cfg.range_lookback + 2) : -2]
        resistance = max(candle.high for candle in base)
        support = min(candle.low for candle in base)
        buffer = current_atr * cfg.breakout_buffer_atr
        tolerance = current_atr * cfg.retest_tolerance_atr

        bias: Literal["BULLISH", "BEARISH", "NEUTRAL"]
        if fast > slow and confirmation.close > fast:
            bias = "BULLISH"
        elif fast < slow and confirmation.close < fast:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        common = {
            "timestamp": confirmation.timestamp,
            "bias": bias,
            "strategy": self.name,
            "atr": current_atr,
            "fast_ema": fast,
            "slow_ema": slow,
        }

        long_breakout = breakout.close > resistance + buffer
        long_retest = (
            confirmation.low <= resistance + tolerance
            and confirmation.close > resistance
            and confirmation.close >= confirmation.open
        )
        if bias == "BULLISH" and long_breakout and long_retest:
            stop = min(confirmation.low, resistance) - cfg.stop_buffer_atr * current_atr
            risk = confirmation.close - stop
            return MarketSetup(
                decision="READY_LONG",
                reason="Bullish range breakout confirmed by a successful retest.",
                entry=confirmation.close,
                stop=stop,
                target=confirmation.close + cfg.reward_to_risk * risk,
                breakout_level=resistance,
                **common,
            )
        if bias == "BULLISH" and long_breakout:
            return MarketSetup(
                decision="WATCH_LONG",
                reason="Bullish breakout detected; waiting for a retest and confirming close.",
                breakout_level=resistance,
                **common,
            )

        short_breakout = breakout.close < support - buffer
        short_retest = (
            confirmation.high >= support - tolerance
            and confirmation.close < support
            and confirmation.close <= confirmation.open
        )
        if bias == "BEARISH" and short_breakout and short_retest:
            stop = max(confirmation.high, support) + cfg.stop_buffer_atr * current_atr
            risk = stop - confirmation.close
            return MarketSetup(
                decision="READY_SHORT",
                reason="Bearish range breakdown confirmed by a failed retest.",
                entry=confirmation.close,
                stop=stop,
                target=confirmation.close - cfg.reward_to_risk * risk,
                breakout_level=support,
                **common,
            )
        if bias == "BEARISH" and short_breakout:
            return MarketSetup(
                decision="WATCH_SHORT",
                reason="Bearish breakdown detected; waiting for a retest and confirming close.",
                breakout_level=support,
                **common,
            )

        return MarketSetup(
            decision="NO_TRADE",
            reason="No completed breakout-and-retest sequence matches the trend filter.",
            **common,
        )


@dataclass(frozen=True)
class PullbackConfig:
    fast_ema_period: int = 9
    slow_ema_period: int = 21
    atr_period: int = 14
    pullback_tolerance_atr: float = 0.20
    stop_buffer_atr: float = 0.25
    reward_to_risk: float = 1.5


class PullbackStrategy:
    name = "ema_pullback"

    def __init__(self, config: PullbackConfig | None = None) -> None:
        self.config = config or PullbackConfig()

    @property
    def minimum_candles(self) -> int:
        cfg = self.config
        return max(cfg.slow_ema_period + 1, cfg.atr_period + 1)

    def analyze(self, candles: list[Candle]) -> MarketSetup:
        if len(candles) < self.minimum_candles:
            raise ValueError(
                f"Strategy requires at least {self.minimum_candles} completed candles; "
                f"received {len(candles)}"
            )

        ordered = sorted(candles, key=lambda candle: candle.timestamp)
        cfg = self.config
        closes = [candle.close for candle in ordered]
        previous = ordered[-2]
        confirmation = ordered[-1]
        fast = ema(closes, cfg.fast_ema_period)
        slow = ema(closes, cfg.slow_ema_period)
        previous_fast = ema(closes[:-1], cfg.fast_ema_period)
        previous_slow = ema(closes[:-1], cfg.slow_ema_period)
        current_atr = atr(ordered, cfg.atr_period)
        tolerance = current_atr * cfg.pullback_tolerance_atr

        bias: Literal["BULLISH", "BEARISH", "NEUTRAL"]
        if fast > slow and confirmation.close > fast:
            bias = "BULLISH"
        elif fast < slow and confirmation.close < fast:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        common = {
            "timestamp": confirmation.timestamp,
            "bias": bias,
            "strategy": self.name,
            "atr": current_atr,
            "fast_ema": fast,
            "slow_ema": slow,
        }

        long_pullback = (
            previous.low <= previous_fast + tolerance
            and previous.close >= previous_slow
        )
        long_confirmation = (
            confirmation.close > previous.high
            and confirmation.close >= confirmation.open
        )
        if bias == "BULLISH" and long_pullback and long_confirmation:
            stop = min(previous.low, confirmation.low) - cfg.stop_buffer_atr * current_atr
            risk = confirmation.close - stop
            return MarketSetup(
                decision="READY_LONG",
                reason="Bullish EMA pullback resumed through the prior candle high.",
                entry=confirmation.close,
                stop=stop,
                target=confirmation.close + cfg.reward_to_risk * risk,
                breakout_level=previous.high,
                **common,
            )
        if bias == "BULLISH" and long_pullback:
            return MarketSetup(
                decision="WATCH_LONG",
                reason="Bullish EMA pullback detected; waiting for continuation.",
                breakout_level=previous.high,
                **common,
            )

        short_pullback = (
            previous.high >= previous_fast - tolerance
            and previous.close <= previous_slow
        )
        short_confirmation = (
            confirmation.close < previous.low
            and confirmation.close <= confirmation.open
        )
        if bias == "BEARISH" and short_pullback and short_confirmation:
            stop = max(previous.high, confirmation.high) + cfg.stop_buffer_atr * current_atr
            risk = stop - confirmation.close
            return MarketSetup(
                decision="READY_SHORT",
                reason="Bearish EMA pullback resumed through the prior candle low.",
                entry=confirmation.close,
                stop=stop,
                target=confirmation.close - cfg.reward_to_risk * risk,
                breakout_level=previous.low,
                **common,
            )
        if bias == "BEARISH" and short_pullback:
            return MarketSetup(
                decision="WATCH_SHORT",
                reason="Bearish EMA pullback detected; waiting for continuation.",
                breakout_level=previous.low,
                **common,
            )

        return MarketSetup(
            decision="NO_TRADE",
            reason="No confirmed EMA pullback-continuation setup.",
            **common,
        )


@dataclass(frozen=True)
class DemandZoneConfig:
    fast_ema_period: int = 9
    slow_ema_period: int = 21
    atr_period: int = 14
    zone_lookback: int = 80
    pivot_radius: int = 2
    minimum_touches: int = 2
    minimum_touch_separation: int = 3
    zone_tolerance_atr: float = 0.25
    invalidation_buffer_atr: float = 0.10
    stop_buffer_atr: float = 0.25
    reward_to_risk: float = 1.5


@dataclass(frozen=True)
class _PriceZone:
    low: float
    high: float
    last_touch_index: int
    touches: int


class DemandZoneStrategy:
    name = "demand_zone_rejection"

    def __init__(self, config: DemandZoneConfig | None = None) -> None:
        self.config = config or DemandZoneConfig()

    @property
    def minimum_candles(self) -> int:
        cfg = self.config
        return max(cfg.zone_lookback + 2, cfg.slow_ema_period + 1, cfg.atr_period + 1)

    def analyze(self, candles: list[Candle]) -> MarketSetup:
        if len(candles) < self.minimum_candles:
            raise ValueError(
                f"Strategy requires at least {self.minimum_candles} completed candles; "
                f"received {len(candles)}"
            )

        ordered = sorted(candles, key=lambda candle: candle.timestamp)
        cfg = self.config
        closes = [candle.close for candle in ordered]
        rejection = ordered[-2]
        confirmation = ordered[-1]
        history = ordered[-(cfg.zone_lookback + 2) : -2]
        fast = ema(closes, cfg.fast_ema_period)
        slow = ema(closes, cfg.slow_ema_period)
        previous_fast = ema(closes[:-1], cfg.fast_ema_period)
        current_atr = atr(ordered, cfg.atr_period)
        tolerance = current_atr * cfg.zone_tolerance_atr
        invalidation_buffer = current_atr * cfg.invalidation_buffer_atr

        if confirmation.close > fast and fast > previous_fast:
            bias: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "BULLISH"
        elif confirmation.close < fast and fast < previous_fast:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        common = {
            "timestamp": confirmation.timestamp,
            "bias": bias,
            "strategy": self.name,
            "atr": current_atr,
            "fast_ema": fast,
            "slow_ema": slow,
        }

        demand = self._find_zone(
            history,
            tolerance=tolerance,
            invalidation_buffer=invalidation_buffer,
            kind="demand",
            probe=rejection,
        )
        if demand is not None and self._bullish_rejection(rejection, demand):
            long_confirmation = (
                bias == "BULLISH"
                and confirmation.close > rejection.high
                and confirmation.close >= confirmation.open
            )
            if long_confirmation:
                stop = min(rejection.low, demand.low) - cfg.stop_buffer_atr * current_atr
                risk = confirmation.close - stop
                return MarketSetup(
                    decision="READY_LONG",
                    reason=(
                        f"Demand zone held after {demand.touches} touches; bullish rejection "
                        "confirmed above the rejection candle high."
                    ),
                    entry=confirmation.close,
                    stop=stop,
                    target=confirmation.close + cfg.reward_to_risk * risk,
                    breakout_level=rejection.high,
                    zone_low=demand.low,
                    zone_high=demand.high,
                    **common,
                )
            return MarketSetup(
                decision="WATCH_LONG",
                reason="Demand zone rejected; waiting for bullish confirmation and EMA recovery.",
                breakout_level=rejection.high,
                zone_low=demand.low,
                zone_high=demand.high,
                **common,
            )

        supply = self._find_zone(
            history,
            tolerance=tolerance,
            invalidation_buffer=invalidation_buffer,
            kind="supply",
            probe=rejection,
        )
        if supply is not None and self._bearish_rejection(rejection, supply):
            short_confirmation = (
                bias == "BEARISH"
                and confirmation.close < rejection.low
                and confirmation.close <= confirmation.open
            )
            if short_confirmation:
                stop = max(rejection.high, supply.high) + cfg.stop_buffer_atr * current_atr
                risk = stop - confirmation.close
                return MarketSetup(
                    decision="READY_SHORT",
                    reason=(
                        f"Supply zone held after {supply.touches} touches; bearish rejection "
                        "confirmed below the rejection candle low."
                    ),
                    entry=confirmation.close,
                    stop=stop,
                    target=confirmation.close - cfg.reward_to_risk * risk,
                    breakout_level=rejection.low,
                    zone_low=supply.low,
                    zone_high=supply.high,
                    **common,
                )
            return MarketSetup(
                decision="WATCH_SHORT",
                reason="Supply zone rejected; waiting for bearish confirmation and EMA rollover.",
                breakout_level=rejection.low,
                zone_low=supply.low,
                zone_high=supply.high,
                **common,
            )

        return MarketSetup(
            decision="NO_TRADE",
            reason="No valid multi-touch demand or supply zone rejection is awaiting confirmation.",
            **common,
        )

    def _find_zone(
        self,
        candles: list[Candle],
        *,
        tolerance: float,
        invalidation_buffer: float,
        kind: Literal["demand", "supply"],
        probe: Candle,
    ) -> _PriceZone | None:
        cfg = self.config
        pivots: list[tuple[int, float]] = []
        for index in range(cfg.pivot_radius, len(candles) - cfg.pivot_radius):
            window = candles[index - cfg.pivot_radius : index + cfg.pivot_radius + 1]
            price = candles[index].low if kind == "demand" else candles[index].high
            extreme = (
                min(item.low for item in window)
                if kind == "demand"
                else max(item.high for item in window)
            )
            if price == extreme:
                pivots.append((index, price))

        candidates: list[_PriceZone] = []
        seen: set[tuple[int, ...]] = set()
        for _, anchor_price in pivots:
            nearby = [pivot for pivot in pivots if abs(pivot[1] - anchor_price) <= tolerance]
            separated: list[tuple[int, float]] = []
            for pivot in nearby:
                if not separated or pivot[0] - separated[-1][0] >= cfg.minimum_touch_separation:
                    separated.append(pivot)
            if len(separated) < cfg.minimum_touches:
                continue
            key = tuple(index for index, _ in separated)
            if key in seen:
                continue
            seen.add(key)
            prices = [price for _, price in separated]
            zone_low = min(prices)
            zone_high = max(prices)
            if zone_high - zone_low < tolerance:
                if kind == "demand":
                    zone_high = zone_low + tolerance
                else:
                    zone_low = zone_high - tolerance
            last_touch_index = separated[-1][0]
            later = candles[last_touch_index + 1 :]
            if kind == "demand":
                invalidated = any(
                    candle.close < zone_low - invalidation_buffer for candle in later
                )
            else:
                invalidated = any(
                    candle.close > zone_high + invalidation_buffer for candle in later
                )
            if not invalidated:
                zone = _PriceZone(zone_low, zone_high, last_touch_index, len(separated))
                if probe.low <= zone.high and probe.high >= zone.low:
                    candidates.append(zone)

        if not candidates:
            return None
        return max(candidates, key=lambda zone: (zone.last_touch_index, zone.touches))

    @staticmethod
    def _bullish_rejection(candle: Candle, zone: _PriceZone) -> bool:
        return (
            candle.low <= zone.high
            and candle.high >= zone.low
            and candle.close > zone.high
            and candle.close >= candle.open
        )

    @staticmethod
    def _bearish_rejection(candle: Candle, zone: _PriceZone) -> bool:
        return (
            candle.high >= zone.low
            and candle.low <= zone.high
            and candle.close < zone.low
            and candle.close <= candle.open
        )


@dataclass(frozen=True)
class TrendFilterConfig:
    fast_ema_period: int = 20
    slow_ema_period: int = 50


class HigherTimeframeTrendFilter:
    name = "hourly_ema_trend"

    def __init__(self, config: TrendFilterConfig | None = None) -> None:
        self.config = config or TrendFilterConfig()

    @property
    def minimum_candles(self) -> int:
        return self.config.slow_ema_period + 1

    def analyze(self, candles: list[Candle]) -> Literal["BULLISH", "BEARISH", "NEUTRAL"]:
        if len(candles) < self.minimum_candles:
            raise ValueError(
                f"Trend filter requires at least {self.minimum_candles} completed candles; "
                f"received {len(candles)}"
            )

        ordered = sorted(candles, key=lambda candle: candle.timestamp)
        closes = [candle.close for candle in ordered]
        fast = ema(closes, self.config.fast_ema_period)
        slow = ema(closes, self.config.slow_ema_period)
        previous_fast = ema(closes[:-1], self.config.fast_ema_period)
        latest = ordered[-1]
        if fast > slow and fast > previous_fast and latest.close > fast:
            return "BULLISH"
        if fast < slow and fast < previous_fast and latest.close < fast:
            return "BEARISH"
        return "NEUTRAL"
