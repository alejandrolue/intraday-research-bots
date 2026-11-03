from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

from goldbot.models import Candle  # noqa: E402
from fib_retrace import Rule, confluent, setups, simulate  # noqa: E402

T0 = datetime(2020, 3, 2, tzinfo=timezone.utc)


def series(closes):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(Candle(T0 + timedelta(hours=i), prev, max(prev, c) + 0.2, min(prev, c) - 0.2, c, spread=0.1))
        prev = c
    return out


def leg_up_then_pullback(pullback_to):
    up = [100.0] * 6 + [100.0 - 2] + [100.0 + 2 * i for i in range(1, 8)]   # swing low 98, rally to 114
    top = [113.0, 112.0, 111.5]                                            # confirms the swing high
    down = [111.0 - (111.0 - pullback_to) * k / 4 for k in range(1, 5)]
    after = [pullback_to + 2 * k for k in range(1, 12)]
    return series(up + top + down + after)


def test_setups_find_the_leg_after_confirmation():
    bars = leg_up_then_pullback(106.0)
    legs = [leg for leg in setups(bars) if leg["direction"] == 1]
    assert legs and legs[-1]["end"] > legs[-1]["start"]
    assert legs[-1]["confirmed"] - legs[-1]["end_index"] == 3   # no lookahead: 3 candles after the high


def test_long_fills_at_the_level_and_reaches_the_leg_end():
    bars = leg_up_then_pullback(106.0)
    legs = setups(bars)
    bias = ["BULLISH"] * len(bars)
    trades = simulate(bars, Rule(0.5, False, "high"), legs, bias, [None] * len(bars),
                      date(2020, 1, 1), date(2021, 1, 1), financing_rate=0.0)
    assert trades and trades[0].direction == 1 and trades[0].reason == "target"


def test_no_trade_against_the_trend():
    bars = leg_up_then_pullback(106.0)
    trades = simulate(bars, Rule(0.5, False, "high"), setups(bars), ["BEARISH"] * len(bars),
                      [None] * len(bars), date(2020, 1, 1), date(2021, 1, 1), financing_rate=0.0)
    assert not [t for t in trades if t.direction == 1]


def test_confluence_detects_round_numbers_swings_and_the_ema():
    assert confluent(4096.0, 4108.0, [], None, 0.5)            # 4100 inside
    assert not confluent(4061.0, 4069.0, [], None, 0.5)
    assert confluent(4061.0, 4069.0, [4065.0], None, 0.5)
    assert confluent(4061.0, 4069.0, [], 4062.0, 0.5)
