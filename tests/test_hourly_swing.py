from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

from goldbot.models import Candle  # noqa: E402
from hourly_swing import Features, Rule, signal, simulate  # noqa: E402

T0 = datetime(2020, 3, 2, tzinfo=timezone.utc)
SIDEWAYS = Features(atr=1.0, efficiency=0.1, adx=15.0, prior_high=110.0, prior_low=100.0)


def candle(o, h, l, c, i=0, spread=0.0):
    return Candle(T0 + timedelta(hours=i), o, h, l, c, spread=spread)


def test_range_long_at_the_bottom_and_short_at_the_top() -> None:
    rule = Rule("range", "strict", "0.5", "mid")
    long = signal(rule, candle(100.5, 101.5, 99.8, 101.2), SIDEWAYS)
    assert long == (1, pytest.approx(99.3), 105.0)
    short = signal(rule, candle(109.5, 110.3, 108.4, 108.8), SIDEWAYS)
    assert short[0] == -1 and short[1] == pytest.approx(110.8) and short[2] == 105.0


def test_range_needs_a_sideways_state_and_a_rejection() -> None:
    rule = Rule("range", "strict", "0.5", "mid")
    trending = Features(1.0, 0.6, 15.0, 110.0, 100.0)
    assert signal(rule, candle(100.5, 101.5, 99.8, 101.2), trending) is None
    assert signal(rule, candle(101.2, 101.5, 99.8, 100.5), SIDEWAYS) is None  # down candle
    loose = Rule("range", "loose", "0.5", "mid")
    adx22 = Features(1.0, 0.1, 22.0, 110.0, 100.0)
    assert signal(rule, candle(100.5, 101.5, 99.8, 101.2), adx22) is None
    assert signal(loose, candle(100.5, 101.5, 99.8, 101.2), adx22) is not None


def test_breakout_after_a_narrow_range() -> None:
    rule = Rule("breakout", "3", "mid", "2")
    narrow = Features(atr=1.0, efficiency=0.2, adx=15.0, prior_high=102.0, prior_low=100.0)
    long = signal(rule, candle(101.8, 103.0, 101.7, 102.9), narrow)
    assert long == (1, 101.0, pytest.approx(102.9 + 2 * 1.9))
    wide = Features(1.0, 0.2, 15.0, 106.0, 100.0)
    assert signal(rule, candle(105.8, 107.0, 105.7, 106.9), wide) is None


def test_simulator_stop_first_time_exit_and_financing() -> None:
    rule = Rule("range", "strict", "0.5", "mid")
    feats = [SIDEWAYS] + [None] * 40
    hit_both = [candle(100.5, 101.5, 99.8, 101.2, 0), candle(101.2, 106.0, 98.0, 101.0, 1)]
    trades = simulate("X", hit_both + [candle(101, 101, 101, 101, 2)], feats, rule,
                      date(2020, 1, 1), date(2021, 1, 1), financing_rate=0.0)
    assert trades[0].reason == "stop" and trades[0].exit == pytest.approx(99.3)
    flat = [candle(100.5, 101.5, 99.8, 101.2, 0)] + [candle(101.2, 101.4, 101.0, 101.2, i) for i in range(1, 40)]
    trades = simulate("X", flat, feats, rule, date(2020, 1, 1), date(2021, 1, 1), financing_rate=0.365)
    assert trades[0].reason == "time" and trades[0].financing_points > 0
