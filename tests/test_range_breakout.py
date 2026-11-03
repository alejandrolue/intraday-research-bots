from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from goldbot.cli import _daily_flat_guard, parse_flat_at
from goldbot.models import Candle
from goldbot.range_breakout import RangeBreakoutStrategy

# Tuesday 4 March 2025: London is on GMT, so local time == UTC.
DAY = datetime(2025, 3, 4, tzinfo=timezone.utc)


def day_candles(after: list[tuple[float, float, float]]) -> list[Candle]:
    """Quiet Asia range 2895-2905 from 00:00 to 07:00, then the given (high, low, close) 5m candles."""
    out = []
    t = DAY
    while t < DAY + timedelta(hours=7):
        out.append(Candle(t, 2900, 2905 if t.minute == 0 else 2902, 2895 if t.minute == 5 else 2898, 2900, spread=0.3))
        t += timedelta(minutes=5)
    for high, low, close in after:
        out.append(Candle(t, min(max(out[-1].close, low), high), high, low, close, spread=0.3))
        t += timedelta(minutes=5)
    return out


def test_first_break_up_gives_a_long_with_the_stop_at_the_range_low():
    setup = RangeBreakoutStrategy("rangebreak-london").analyze(day_candles([(2903, 2899, 2902), (2906, 2901, 2905.5)]))
    assert setup.decision == "READY_LONG"
    assert setup.stop == 2895 and setup.entry == 2905.5 and setup.target > setup.entry + 100


def test_only_the_first_break_of_the_day_counts():
    candles = day_candles([(2906, 2901, 2905.5), (2904, 2899, 2900), (2900, 2893, 2894)])
    assert RangeBreakoutStrategy("rangebreak-london").analyze(candles).decision == "NO_TRADE"


def test_a_candle_breaking_both_sides_gives_nothing():
    candles = day_candles([(2906, 2894, 2900)])
    assert RangeBreakoutStrategy("rangebreak-london").analyze(candles).decision == "NO_TRADE"


def test_a_touch_without_a_spread_of_room_is_not_a_break():
    candles = day_candles([(2905.2, 2900, 2905.1)])
    assert RangeBreakoutStrategy("rangebreak-london").analyze(candles).decision == "NO_TRADE"


def test_no_signal_after_the_window():
    quiet = [(2903, 2899, 2900)] * 60          # 07:00 .. 12:00
    candles = day_candles(quiet + [(2907, 2901, 2906)])
    assert RangeBreakoutStrategy("rangebreak-london").analyze(candles).decision == "NO_TRADE"


def test_unknown_name_is_rejected():
    with pytest.raises(ValueError):
        RangeBreakoutStrategy("rangebreak-mars")


def test_parse_flat_at():
    zone, at = parse_flat_at("Europe/London 16:00")
    assert str(zone) == "Europe/London" and (at.hour, at.minute) == (16, 0)
    with pytest.raises(ValueError):
        parse_flat_at("16:00")


class FakeExecutor:
    def __init__(self):
        self.reasons = []

    def close_all(self, reason):
        self.reasons.append(reason)
        return [{"event": "closed", "reason": reason}]


def test_daily_flat_guard_blocks_and_closes_after_the_flat_time():
    runner = SimpleNamespace(entry_block=None, ledger=SimpleNamespace(open_positions=[object()]))
    executor = FakeExecutor()
    # 16:00 London in summer (BST) is 15:00 UTC
    _daily_flat_guard("Europe/London 16:00", datetime(2025, 7, 1, 14, 59, tzinfo=timezone.utc), runner, executor, None)
    assert runner.entry_block is None and executor.reasons == []
    _daily_flat_guard("Europe/London 16:00", datetime(2025, 7, 1, 15, 0, tzinfo=timezone.utc), runner, executor, None)
    assert "daily close" in runner.entry_block and executor.reasons == ["END_OF_DAY"]


def test_daily_flat_guard_keeps_the_weekend_message():
    runner = SimpleNamespace(entry_block="Weekend close in 30 minutes", ledger=SimpleNamespace(open_positions=[]))
    _daily_flat_guard("Europe/London 16:00", datetime(2025, 7, 4, 18, 0, tzinfo=timezone.utc), runner, None, None)
    assert runner.entry_block.startswith("Weekend")


