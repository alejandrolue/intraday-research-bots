from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
import index_open_study as study  # noqa: E402


def test_grid_has_32_unique_variants_over_two_markets() -> None:
    grid = study.variants()
    assert len(grid) == 32
    assert len({variant.id for variant in grid}) == 32
    assert {variant.market for variant in grid} == {"US100", "US30"}
    assert grid[0].id == "US100-orb-nycash-15-rr1.6-none"


def test_nycash_session_opens_at_0930_new_york_across_dst() -> None:
    for day in (datetime(2026, 1, 14, tzinfo=timezone.utc), datetime(2026, 7, 14, tzinfo=timezone.utc)):
        strategy = study.Variant("US100", "orb", 15, 1.6, None).strategy(day)
        assert (strategy.zone, strategy.open_hour, strategy.open_minute) == (
            ZoneInfo("America/New_York"), 9, 30
        )


def row(market: str, family: str, trades: int, average: float, label: str) -> dict:
    return {
        "variant": {"market": market, "family": family}, "trades": trades,
        "average_r": average, "max_drawdown_r": 5.0, "label": label,
        "side": {"BUY": {"total_r": 1.0}, "SELL": {"total_r": 1.0}}, "quarters": [1, 1, 1, -1],
    }


def test_selection_locks_one_variant_per_market_and_family() -> None:
    rows = [
        row(market, family, 200, average, f"{market}-{family}-{average}")
        for market in study.MARKETS
        for family in ("orb", "rolling_sweep")
        for average in (0.02, 0.08)
    ]

    selected = study.select(rows)

    assert len(selected) == 4
    assert all(choice["eligible"] and choice["id"].endswith("0.08") for choice in selected)
