from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
import orb_flux_study as study  # noqa: E402


def test_grid_and_thresholds_follow_the_protocol() -> None:
    for timeframe, minimum in (("5m", 150), ("1m", 75)):
        grid = study.variants(timeframe)
        assert len(grid) == 96 and len({v.id for v in grid}) == 96
        assert {v.market for v in grid} == {"US500", "US100"}
        assert {v.minutes for v in grid} == {5, 15, 30, 60}
        assert study.TIMEFRAMES[timeframe]["min_trades"] == minimum


def test_selection_locks_one_per_market_and_stop_type() -> None:
    rows = [
        {"variant": {"timeframe": "1m", "market": m, "family": f}, "trades": 90, "average_r": a,
         "max_drawdown_r": 5.0, "label": f"{m}-{f}-{a}",
         "side": {"BUY": {"total_r": 1.0}, "SELL": {"total_r": 1.0}}, "buckets": [1, 1, 1, 1, -1, -1]}
        for m in study.MARKETS for f in ("orbside", "orbmid") for a in (0.02, 0.09)
    ]
    selected = study.select(rows)
    assert len(selected) == 4
    assert all(choice["eligible"] and choice["id"].endswith("0.09") for choice in selected)
