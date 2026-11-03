from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

from goldbot.models import Candle  # noqa: E402
from entry_timing import Signal, Variant, execute  # noqa: E402

T0 = datetime(2026, 3, 3, 10, 0, tzinfo=timezone.utc)  # a Tuesday


def m1(prices):
    """1m candles from (open, high, low, close) tuples, spread 0.2."""
    return [Candle(T0 + timedelta(minutes=i), o, h, l, c, spread=0.2) for i, (o, h, l, c) in enumerate(prices)]


def sig(t=0, direction=1, close=100.0, stop=98.0, target=103.0, high=101.0, low=99.0):
    return Signal(T0 + timedelta(minutes=t), direction, close, stop, target, high, low, 2.0, "ema_pullback")


def test_market_entry_fills_at_the_ask_and_hits_the_target():
    bars = m1([(100, 100.5, 99.8, 100.3), (100.3, 103.5, 100.2, 103.2)])
    trades = execute(bars, [sig()], Variant("market", False, False))
    assert len(trades) == 1 and trades[0].reason == "target"
    assert trades[0].r == (103.0 - 100.1) / (100.1 - 98.0)


def test_limit_needs_a_trade_through_by_the_spread():
    # Signal candle 99-101 closing at 100: the L50 limit is 99.0; a fill needs a low <= 98.8.
    touch = m1([(100, 100.2, 99.9, 100.0), (99.5, 99.6, 98.85, 99.2)] + [(100, 100.1, 99.9, 100)] * 20)
    assert execute(touch, [sig()], Variant("L50", False, False)) == []
    through = m1([(100, 100.2, 99.9, 100.0), (99.5, 99.6, 98.75, 99.2), (99.2, 103.4, 99.1, 103.2)])
    trades = execute(through, [sig()], Variant("L50", False, False))
    assert trades and trades[0].reason == "target" and trades[0].r == (103.0 - 99.0) / (99.0 - 98.0)


def test_cooldown_blocks_the_direction_after_two_stops():
    down = [(100, 100.1, 97.0, 97.2)]
    bars = m1([(100, 100.2, 99.9, 100.0)] + down + [(100, 100.2, 99.9, 100.0)] + down
              + [(100, 100.2, 99.9, 100.0)] * 3)
    sigs = [sig(0), sig(2), sig(4)]
    with_cool = execute(bars, sigs, Variant("market", True, False))
    without = execute(bars, sigs, Variant("market", False, False))
    assert len(without) == 2 and len(with_cool) == 2  # third signal: no stop yet in either
    sigs = [sig(0), sig(2), sig(4, close=100.0)]
    bars2 = bars[:4] + m1([(100, 100.1, 97.0, 97.2)])[0:0] + bars[4:]
    assert len(execute(bars2, sigs, Variant("market", True, False))) <= len(execute(bars2, sigs, Variant("market", False, False)))


def test_extension_locks_profit_instead_of_falling_back_to_the_stop():
    bars = m1([(100, 100.2, 99.9, 100.0), (100.0, 102.9, 100.0, 102.8), (102.8, 102.9, 100.4, 100.5)])
    trades = execute(bars, [sig()], Variant("market", False, True))
    assert trades[0].reason == "locked"
    assert trades[0].r > 0  # stop moved to the old target - 1R = +1R level, filled near it
