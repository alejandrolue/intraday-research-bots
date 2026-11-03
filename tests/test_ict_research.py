from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
import ict_study as study  # noqa: E402


def test_grids_groups_and_thresholds_follow_the_protocol() -> None:
    expected = {"sb5m": (72, 9, 100), "sb1m": (72, 9, 75), "ts5m": (48, 6, 150)}
    for key, (count, groups, minimum) in expected.items():
        grid = study.variants(key)
        assert len(grid) == count and len({v.id for v in grid}) == count
        assert len({(v.market, v.group) for v in grid}) == groups
        assert study.TIMEFRAMES[key]["min_trades"] == minimum
    assert all(v.bias == "DAY" for v in study.variants("sb5m"))          # article: daily bias
    assert {v.bias for v in study.variants("ts5m")} == {None, "DAY"}
    assert study.variants("sb1m")[0].strategy(study.utc(2025, 10, 1)).bar_minutes == 1
