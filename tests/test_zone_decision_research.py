from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

from goldbot.models import Candle, MarketSetup
from zone_decision_strategies import Variant, ZoneDecisionStrategy
from zone_decision_study import variants


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bar(index: int, close: float) -> Candle:
    return Candle(START + timedelta(minutes=5 * index), close - 0.2, close + 0.8, close - 0.8, close, spread=0.5)


def rejection_candles() -> list[Candle]:
    candles = [bar(index, 100 + index * 0.03) for index in range(50)]
    candles[20] = Candle(candles[20].timestamp, 96, 97, 94, 96)
    candles[30] = Candle(candles[30].timestamp, 103, 110, 102, 103)
    candles[-2] = Candle(candles[-2].timestamp, 95.0, 96.0, 94.2, 95.6)
    candles[-1] = Candle(candles[-1].timestamp, 95.7, 97.4, 95.5, 97.2)
    return candles


def breakout_candles() -> list[Candle]:
    candles = [bar(index, 100 + index * 0.02) for index in range(50)]
    candles[20] = Candle(candles[20].timestamp, 102, 105, 101, 102)
    candles[30] = Candle(candles[30].timestamp, 104, 115, 103, 104)
    candles[-2] = Candle(candles[-2].timestamp, 104, 107, 103.8, 106.5)
    candles[-1] = Candle(candles[-1].timestamp, 105.2, 107.2, 104.8, 106.8)
    return candles


def mirror(candles: list[Candle], axis: float = 200) -> list[Candle]:
    return [
        Candle(item.timestamp, axis - item.open, axis - item.low, axis - item.high,
               axis - item.close, item.volume, item.spread)
        for item in candles
    ]


def variant(family: str, parameter: float = 1.5) -> Variant:
    return Variant(family, 40, 2, parameter, None, "all")


def test_grid_is_pre_registered_at_96_symmetric_variants() -> None:
    rows = variants()
    assert len(rows) == 96
    assert {item.family for item in rows} == {"ema_room", "rejection", "breakout_retest"}


def test_rejection_strategy_is_symmetric() -> None:
    long = ZoneDecisionStrategy(variant("rejection", 1.0)).analyze(rejection_candles())
    short = ZoneDecisionStrategy(variant("rejection", 1.0)).analyze(mirror(rejection_candles()))
    assert long.direction == "BUY"
    assert short.direction == "SELL"
    assert long.zone_low is not None and long.stop < long.zone_low
    assert short.zone_high is not None and short.stop > short.zone_high


def test_breakout_retest_strategy_is_symmetric() -> None:
    long = ZoneDecisionStrategy(variant("breakout_retest")).analyze(breakout_candles())
    short = ZoneDecisionStrategy(variant("breakout_retest")).analyze(mirror(breakout_candles()))
    assert long.direction == "BUY"
    assert short.direction == "SELL"


class AlwaysLong:
    minimum_candles = 2

    def analyze(self, candles: list[Candle]) -> MarketSetup:
        bar = candles[-1]
        return MarketSetup(
            "READY_LONG", bar.timestamp, "scripted", "BULLISH", "scripted",
            entry=bar.close, stop=bar.close - 2, target=bar.close + 3,
        )


def test_ema_room_blocks_a_nearby_opposing_zone() -> None:
    candles = breakout_candles()
    candles[-2] = bar(48, 103.0)
    candles[-1] = bar(49, 103.5)
    blocked = ZoneDecisionStrategy(
        variant("ema_room", 1.5), base_strategy=AlwaysLong()
    ).analyze(candles)
    assert not blocked.ready
    assert "Opposing zone" in blocked.reason


def test_changing_an_unseen_future_bar_cannot_change_a_signal() -> None:
    candles = rejection_candles()
    first = ZoneDecisionStrategy(variant("rejection")).analyze(candles)
    future = replace(bar(50, 130), timestamp=candles[-1].timestamp + timedelta(minutes=5))
    replay = ZoneDecisionStrategy(variant("rejection")).analyze(candles)
    assert first == replay
    assert future.timestamp > first.timestamp
