from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

from goldbot.models import Candle  # noqa: E402
from trend_daily import Rule, simulate, target  # noqa: E402

START = datetime(2020, 1, 1, tzinfo=timezone.utc)


def bars(closes: list[float], spread: float = 0.2, gap_at: int | None = None) -> list[Candle]:
    out, previous = [], closes[0]
    for i, close in enumerate(closes):
        opened = previous if gap_at != i else close
        out.append(Candle(START + timedelta(days=i), opened, max(opened, close) + 0.5,
                          min(opened, close) - 0.5, close, spread=spread))
        previous = close
    return out


def test_donchian_enters_on_breakout_and_exits_on_the_short_channel() -> None:
    rule = Rule("donchian", 5, 3, 3.0)
    series = bars([100.0] * 8 + [103.0, 104, 105, 106, 104, 101, 100, 100, 100])
    assert target(rule, series, 8, 0) == 1        # close above the prior 5 highs
    trades = simulate("X", series, rule, date(2020, 1, 1), date(2021, 1, 1), financing_rate=0.0, atr_period=2)
    first = trades[0]
    assert first.direction == 1 and first.opened == date(2020, 1, 10)
    assert first.entry == pytest.approx(series[9].open + 0.1)   # next open + half spread
    assert first.reason in ("signal", "stop")


def test_gap_through_the_stop_fills_at_the_worse_open() -> None:
    rule = Rule("tsmom", 3, 0, 1.0)
    closes = [100.0, 100, 100, 101, 102, 103, 104, 90, 90]
    series = bars(closes, gap_at=7)
    trades = simulate("X", series, rule, date(2020, 1, 1), date(2021, 1, 1), financing_rate=0.0, atr_period=2)
    stopped = [t for t in trades if t.reason == "stop"][0]
    assert stopped.exit == pytest.approx(90 - 0.1)             # the open, not the stop level
    assert stopped.r < -1.0


def test_financing_is_charged_for_every_calendar_night() -> None:
    rule = Rule("ma", 2, 3, 50.0)
    closes = [100.0, 99, 98, 101, 102, 103, 104, 105, 104, 103, 99, 95, 94]
    with_fee = simulate("X", bars(closes), rule, date(2020, 1, 1), date(2021, 1, 1), financing_rate=0.365, atr_period=2)
    free = simulate("X", bars(closes), rule, date(2020, 1, 1), date(2021, 1, 1), financing_rate=0.0, atr_period=2)
    assert len(with_fee) == len(free)
    long = with_fee[0]
    nights = (long.closed - long.opened).days
    assert long.financing_points == pytest.approx(0.001 * sum(
        b.close for b in bars(closes) if long.opened <= b.timestamp.date() < long.closed), rel=1e-6)
    assert nights > 0 and with_fee[0].r < free[0].r


def test_always_in_rule_waits_for_a_flip_after_a_stop() -> None:
    rule = Rule("tsmom", 2, 0, 0.5)
    closes = [100.0, 100, 101, 102, 99.5, 103, 104, 105, 106, 104, 102, 100]
    trades = simulate("X", bars(closes), rule, date(2020, 1, 1), date(2021, 1, 1), financing_rate=0.0, atr_period=2)
    after_stop = [t for t in trades if t.reason == "stop"]
    assert after_stop
    stopped = after_stop[0]
    later_longs = [t for t in trades if t.opened > stopped.closed and t.direction == stopped.direction]
    shorts_between = [t for t in trades if t.opened > stopped.closed and t.direction != stopped.direction]
    assert not later_longs or (shorts_between and shorts_between[0].opened < later_longs[0].opened)


def test_entries_only_inside_the_window_and_closed_at_its_end() -> None:
    rule = Rule("ma", 2, 3, 50.0)
    closes = [100.0 + i for i in range(20)]
    trades = simulate("X", bars(closes), rule, date(2020, 1, 6), date(2020, 1, 12), financing_rate=0.0, atr_period=2)
    assert all(date(2020, 1, 6) <= t.opened < date(2020, 1, 12) for t in trades)
    assert trades[-1].reason == "end" and trades[-1].closed == date(2020, 1, 12)
