from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

from goldbot.models import Candle  # noqa: E402
from runner_study import EXITS, Signal, replay  # noqa: E402

T0 = datetime(2025, 3, 4, 8, 0, tzinfo=timezone.utc)   # a Tuesday
EX = {e.id: e for e in EXITS}


def bars(path):
    out, prev = [], path[0]
    for i, c in enumerate(path):
        out.append(Candle(T0 + timedelta(minutes=5 * i), prev, max(prev, c), min(prev, c), c, spread=0.0))
        prev = c
    return out


SIG = [Signal(T0, 1, 100.0, 99.0)]   # 1R = 1 point


def test_fixed_target_and_runner_split():
    m5 = bars([100, 101, 102, 103.5, 106, 108, 106.5, 104, 103])
    assert replay(m5, SIG, EX["B"])[0]["r"] == 3.0
    t = replay(m5, SIG, EX["P1.5-T3"])[0]
    # half at 1.5R; runner trails 3R behind the best price (108) -> stopped at 105
    assert t["reason"] == "runner_stop" and abs(t["r"] - (0.75 + 0.5 * 5.0)) < 1e-9


def test_runner_never_loses_after_the_partial():
    m5 = bars([100, 101.6, 100.5, 99.5, 98])
    t = replay(m5, SIG, EX["P1.5-T2"])[0]
    assert abs(t["r"] - 0.75) < 1e-9          # runner stopped at the entry


def test_stop_before_partial_is_a_full_loss():
    m5 = bars([100, 99.5, 98.5])
    assert replay(m5, SIG, EX["P1.5-EOD"])[0]["r"] == -1.0
