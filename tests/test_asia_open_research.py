from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
import asia_open_study as study  # noqa: E402


def test_grid_has_48_unique_variants_over_three_markets() -> None:
    grid = study.variants()
    assert len(grid) == 48
    assert len({variant.id for variant in grid}) == 48
    assert {variant.market for variant in grid} == {"J225", "GOLD", "HK50"}
    assert "J225-orb-tokyo-15-rr1.6-none" in {variant.id for variant in grid}
    assert "HK50-rolling_sweep-hongkong-30-rr2-HOUR_4" in {variant.id for variant in grid}


def test_each_market_uses_its_own_asian_open() -> None:
    day = datetime(2026, 7, 14, tzinfo=timezone.utc)
    tokyo = study.Variant("GOLD", "orb", 15, 1.6, None).strategy(day)
    hongkong = study.Variant("HK50", "orb", 15, 1.6, None).strategy(day)
    assert (tokyo.zone, tokyo.open_hour, tokyo.open_minute) == (ZoneInfo("Asia/Tokyo"), 9, 0)
    assert (hongkong.zone, hongkong.open_hour, hongkong.open_minute) == (ZoneInfo("Asia/Hong_Kong"), 9, 30)


def test_selection_locks_one_per_market_and_family() -> None:
    rows = [
        {"variant": {"market": m, "family": f}, "trades": 200, "average_r": a, "max_drawdown_r": 5.0,
         "label": f"{m}-{f}-{a}", "side": {"BUY": {"total_r": 1.0}, "SELL": {"total_r": 1.0}},
         "quarters": [1, 1, 1, -1]}
        for m in study.MARKETS for f in ("orb", "rolling_sweep") for a in (0.02, 0.08)
    ]
    assert len(study.select(rows)) == 6
