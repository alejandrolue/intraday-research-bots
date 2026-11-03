from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from goldbot.ict_strategies import (
    SilverBulletStrategy, SilverBulletVariant, TurtleSoupStrategy, TurtleSoupVariant,
)
from goldbot.models import Candle

NY = ZoneInfo("America/New_York")


def ny(hour: int, minute: int, day: int = 14) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=NY).astimezone(timezone.utc)


def c(ts: datetime, o: float, h: float, l: float, cl: float) -> Candle:
    return Candle(ts, o, h, l, cl, spread=0.2)


def silver_bullet_day(mirror: bool = False) -> list[Candle]:
    """Pre-window 09:00-10:00 range 99-101; at 10:00 price sweeps below 99, reverses up leaving
    a bullish FVG, then retraces into it."""
    path = []
    t = ny(9, 0)
    for i in range(12):                                   # 09:00-09:55: range 99-101
        path.append((t, 100, 101, 99, 100 + (0.5 if i % 2 else -0.5)))
        t += timedelta(minutes=5)
    path += [
        (ny(10, 0), 100.0, 100.2, 98.2, 98.6),            # sweep below 99
        (ny(10, 5), 98.6, 99.6, 98.5, 99.5),              # closes back above 99; FVG candle 1 (high 99.6)
        (ny(10, 10), 99.5, 101.0, 99.4, 100.9),           # displacement
        (ny(10, 15), 100.9, 101.6, 100.2, 101.4),         # FVG candle 3 (low 100.2 > 99.6)
        (ny(10, 20), 101.4, 101.5, 100.0, 100.6),         # retrace touches the FVG top, closes inside
    ]
    candles = []
    for ts, o, h, l, cl in path:
        if mirror:
            o, h, l, cl = 200 - o, 200 - l, 200 - h, 200 - cl
        candles.append(c(ts, o, h, l, cl))
    warm = [c(ny(8, 0) + timedelta(minutes=5 * i), 100, 100.8, 99.2, 100) for i in range(12)]
    return warm + candles


@pytest.mark.parametrize("stop,target", [("fvg", "rr2"), ("sweep", "rr2"), ("sweep", "liq")])
def test_silver_bullet_long_after_sweep_fvg_and_retrace(stop: str, target: str) -> None:
    strategy = SilverBulletStrategy(SilverBulletVariant("am", "hour", stop, target))
    setup = strategy.analyze(silver_bullet_day())
    if target == "liq" and stop == "sweep":
        # target 101 (range high) is less than 1R above 100.6 when the stop sits under the sweep
        assert setup.decision == "NO_TRADE"
        return
    assert setup.decision == "READY_LONG"
    assert setup.entry == 100.6
    expected_stop = 99.6 if stop == "fvg" else 98.2
    assert expected_stop - 0.5 < setup.stop < expected_stop
    assert round(setup.target - setup.entry, 6) == round(2 * (setup.entry - setup.stop), 6)


def test_silver_bullet_short_is_the_mirror_image() -> None:
    strategy = SilverBulletStrategy(SilverBulletVariant("am", "hour", "fvg", "rr2"))
    long = SilverBulletStrategy(SilverBulletVariant("am", "hour", "fvg", "rr2")).analyze(silver_bullet_day())
    short = strategy.analyze(silver_bullet_day(mirror=True))
    assert short.decision == "READY_SHORT"
    assert round(short.entry, 6) == round(200 - long.entry, 6)
    assert round(short.stop, 6) == round(200 - long.stop, 6)
    assert round(short.target, 6) == round(200 - long.target, 6)


def test_silver_bullet_ignores_other_windows_and_second_touches() -> None:
    candles = silver_bullet_day()
    assert SilverBulletStrategy(SilverBulletVariant("pm", "hour", "fvg", "rr2")).analyze(candles).decision == "NO_TRADE"
    later = candles + [c(ny(10, 25), 100.6, 100.9, 100.1, 100.7)]   # a second touch is not an entry
    assert SilverBulletStrategy(SilverBulletVariant("am", "hour", "fvg", "rr2")).analyze(later).decision == "NO_TRADE"


def turtle_soup_days() -> list[Candle]:
    """Previous day 3990-4040. Today price sweeps below 3990, rallies past the last swing high."""
    candles = [c(ny(23, 55, day=12), 4000, 4001, 3999, 4000)]   # proves day 13 is complete
    t = ny(0, 0, day=13)
    price = 4000.0
    for i in range(288):                                  # previous NY day: 3990-4010
        swing = 10 * ((i % 24) / 12 - 1) if i % 48 < 24 else -10 * ((i % 24) / 12 - 1)
        mid = 4000 + swing
        high = 4040 if i == 100 else min(mid + 1, 4010)   # the previous day's high: 4040
        candles.append(c(t, mid, high, max(mid - 1, 3990), mid))
        t += timedelta(minutes=5)
    path = [4003, 4004, 4006, 4005, 4003, 4001, 3999, 3997, 3995, 3994, 3993, 3992]
    for close in path:                                    # today: drift down, swing high 4007 earlier
        candles.append(c(t, close + 0.5, close + 1.0, close - 1.0, close)); t += timedelta(minutes=5)
    candles.append(c(t, 3992, 3992.5, 3986.0, 3991.0)); t += timedelta(minutes=5)   # sweep below 3990
    for close in (3993, 3996, 4001, 4004):
        candles.append(c(t, close - 1.5, close + 0.5, close - 2, close)); t += timedelta(minutes=5)
    candles.append(c(t, 4004, 4009, 4003, 4008.5))       # closes above the 4007 swing high: MSS
    return candles


def test_turtle_soup_long_after_previous_day_low_sweep_and_structure_shift() -> None:
    strategy = TurtleSoupStrategy(TurtleSoupVariant("day", 2, 1.5))
    candles = turtle_soup_days()
    assert strategy.analyze(candles[:-1]).decision == "NO_TRADE"      # no shift yet
    setup = TurtleSoupStrategy(TurtleSoupVariant("day", 2, 0.1)).analyze(candles)
    assert setup.decision == "READY_LONG"
    assert setup.stop < 3986.0 and setup.target >= 4009


def test_turtle_soup_target_must_meet_min_rr() -> None:
    candles = turtle_soup_days()
    loose = TurtleSoupStrategy(TurtleSoupVariant("day", 2, 0.1)).analyze(candles)
    strict = TurtleSoupStrategy(TurtleSoupVariant("day", 2, 2.5)).analyze(candles)
    assert strict.decision == "NO_TRADE"
    assert loose.decision == "READY_LONG"
    assert round(loose.target, 2) == 4040.0                             # previous day high
    assert 3985.0 < loose.stop < 3986.0


def test_names_parse_and_reject_typos() -> None:
    assert SilverBulletVariant.parse("silverbullet-london-day-sweep-liq").window == "london"
    assert TurtleSoupVariant.parse("turtlesoup-dayh1-r4-rr2.5").radius == 4
    for bad in ("silverbullet-asia-hour-fvg-rr2", "turtlesoup-week-r2-rr1.5"):
        with pytest.raises(ValueError):
            (SilverBulletVariant if bad.startswith("silver") else TurtleSoupVariant).parse(bad)


def test_turtle_soup_needs_a_complete_previous_day() -> None:
    candles = turtle_soup_days()[1:]          # drop the candle that proves day 13 is complete
    assert TurtleSoupStrategy(TurtleSoupVariant("day", 2, 0.1)).analyze(candles).decision == "NO_TRADE"
