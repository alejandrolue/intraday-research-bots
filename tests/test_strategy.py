from __future__ import annotations

from datetime import datetime, timedelta, timezone

from goldbot.models import Candle
from goldbot.strategy import (
    DemandZoneConfig,
    DemandZoneStrategy,
    HigherTimeframeTrendFilter,
    PullbackStrategy,
    RetestStrategy,
)


START = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(START + timedelta(minutes=5 * index), open_, high, low, close, 100, 0.5)


def test_bullish_breakout_and_retest_becomes_ready() -> None:
    candles: list[Candle] = []
    for index in range(58):
        close = 100 + index * 0.10
        candles.append(candle(index, close - 0.05, close + 0.15, close - 0.15, close))
    resistance = max(item.high for item in candles[-20:])
    candles.append(candle(58, resistance + 0.10, resistance + 1.20, resistance + 0.05, resistance + 1.00))
    candles.append(candle(59, resistance + 0.10, resistance + 0.60, resistance + 0.02, resistance + 0.45))

    setup = RetestStrategy().analyze(candles)

    assert setup.decision == "READY_LONG"
    assert setup.entry is not None and setup.stop is not None and setup.target is not None
    assert setup.stop < setup.entry < setup.target


def test_bearish_breakout_and_retest_becomes_ready() -> None:
    candles: list[Candle] = []
    for index in range(58):
        close = 120 - index * 0.10
        candles.append(candle(index, close + 0.05, close + 0.15, close - 0.15, close))
    support = min(item.low for item in candles[-20:])
    candles.append(candle(58, support - 0.10, support - 0.05, support - 1.20, support - 1.00))
    candles.append(candle(59, support - 0.10, support - 0.02, support - 0.60, support - 0.45))

    setup = RetestStrategy().analyze(candles)

    assert setup.decision == "READY_SHORT"
    assert setup.entry is not None and setup.stop is not None and setup.target is not None
    assert setup.target < setup.entry < setup.stop


def test_bullish_ema_pullback_becomes_ready() -> None:
    candles: list[Candle] = []
    for index in range(30):
        close = 100 + index * 0.20
        candles.append(candle(index, close - 0.05, close + 0.12, close - 0.12, close))
    candles.append(candle(30, 105.00, 105.15, 104.72, 104.90))
    candles.append(candle(31, 105.00, 105.70, 104.95, 105.60))

    setup = PullbackStrategy().analyze(candles)

    assert setup.decision == "READY_LONG"
    assert setup.strategy == "ema_pullback"
    assert setup.entry is not None and setup.stop is not None and setup.target is not None
    assert setup.stop < setup.entry < setup.target


def test_bearish_ema_pullback_becomes_ready() -> None:
    candles: list[Candle] = []
    for index in range(30):
        close = 120 - index * 0.20
        candles.append(candle(index, close + 0.05, close + 0.12, close - 0.12, close))
    candles.append(candle(30, 115.00, 115.28, 114.85, 115.10))
    candles.append(candle(31, 115.00, 115.05, 114.30, 114.40))

    setup = PullbackStrategy().analyze(candles)

    assert setup.decision == "READY_SHORT"
    assert setup.strategy == "ema_pullback"
    assert setup.entry is not None and setup.stop is not None and setup.target is not None
    assert setup.target < setup.entry < setup.stop


def zone_strategy() -> DemandZoneStrategy:
    return DemandZoneStrategy(
        DemandZoneConfig(
            zone_lookback=30,
            pivot_radius=1,
            minimum_touch_separation=3,
            zone_tolerance_atr=0.25,
        )
    )


def bullish_zone_candles(*, confirm: bool = True, invalidate: bool = False) -> list[Candle]:
    candles: list[Candle] = []
    for index in range(30):
        base = 103.0 + index * 0.04
        low = base - 0.20
        if index in {6, 16}:
            low = 101.00 + (index - 6) * 0.005
        candles.append(candle(index, base - 0.05, base + 0.25, low, base))
    if invalidate:
        candles[22] = candle(22, 101.2, 101.4, 100.4, 100.5)
    candles.append(candle(30, 101.20, 102.20, 100.90, 102.00))
    confirmation_close = 103.80 if confirm else 102.05
    candles.append(candle(31, 102.10, 104.00, 102.00, confirmation_close))
    return candles


def test_bullish_demand_zone_rejection_becomes_ready() -> None:
    setup = zone_strategy().analyze(bullish_zone_candles())

    assert setup.decision == "READY_LONG"
    assert setup.strategy == "demand_zone_rejection"
    assert setup.zone_low is not None and setup.zone_high is not None
    assert setup.zone_low <= 101.00 <= setup.zone_high
    assert setup.entry is not None and setup.stop is not None and setup.target is not None
    assert setup.stop < setup.entry < setup.target


def test_demand_zone_rejection_waits_for_confirmation() -> None:
    setup = zone_strategy().analyze(bullish_zone_candles(confirm=False))

    assert setup.decision == "WATCH_LONG"
    assert setup.zone_low is not None and setup.zone_high is not None


def test_closed_break_below_demand_zone_invalidates_it() -> None:
    setup = zone_strategy().analyze(bullish_zone_candles(invalidate=True))

    assert setup.decision == "NO_TRADE"


def test_bearish_supply_zone_rejection_becomes_ready() -> None:
    candles: list[Candle] = []
    for index in range(30):
        base = 117.0 - index * 0.04
        high = base + 0.20
        if index in {6, 16}:
            high = 119.00 - (index - 6) * 0.005
        candles.append(candle(index, base + 0.05, high, base - 0.25, base))
    candles.append(candle(30, 118.80, 119.10, 117.80, 118.00))
    candles.append(candle(31, 117.90, 118.00, 116.00, 116.20))

    setup = zone_strategy().analyze(candles)

    assert setup.decision == "READY_SHORT"
    assert setup.zone_low is not None and setup.zone_high is not None
    assert setup.target is not None and setup.entry is not None and setup.stop is not None
    assert setup.target < setup.entry < setup.stop


def test_higher_timeframe_filter_detects_bullish_trend() -> None:
    candles = [
        candle(index, 100 + index * 0.25, 100.2 + index * 0.25, 99.8 + index * 0.25, 100 + index * 0.25)
        for index in range(60)
    ]

    assert HigherTimeframeTrendFilter().analyze(candles) == "BULLISH"


def test_higher_timeframe_filter_detects_bearish_trend() -> None:
    candles = [
        candle(index, 120 - index * 0.25, 120.2 - index * 0.25, 119.8 - index * 0.25, 120 - index * 0.25)
        for index in range(60)
    ]

    assert HigherTimeframeTrendFilter().analyze(candles) == "BEARISH"


def test_adx_is_high_in_trends_and_low_in_ranges() -> None:
    from datetime import datetime, timedelta, timezone

    from goldbot.indicators import adx
    from goldbot.models import Candle

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    trend = [
        Candle(start + timedelta(hours=i), 100 + i, 101 + i, 99.5 + i, 100.8 + i) for i in range(60)
    ]
    chop = [
        Candle(start + timedelta(hours=i), 100, 101, 99, 100 + (0.5 if i % 2 else -0.5))
        for i in range(60)
    ]

    assert adx(trend) > 50
    assert adx(chop) < 20
