from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

import daily_fakeout_study as study  # noqa: E402


def _row(**overrides):
    row = {
        "trades": 600, "average_r": 0.08, "side_r": {"BUY": 10.0, "SELL": 5.0},
        "quarter_r": [1, 1, 1, 1, 1, -1, -1, -1],
        "market_r": {"GOLD": 1.0, "US100": 2.0, "US500": 3.0, "US30": -1.0},
        "month_r": {f"2024-0{m}": (1.0 if m <= 4 else -1.0) for m in range(1, 8)},
        "extra_cost_average_r": 0.02, "max_drawdown_r": 10.0, "id": "x", "variant": {},
    }
    row.update(overrides)
    return row


def test_grid_has_64_distinct_variants() -> None:
    ids = [variant.id for variant in study.variants()]
    assert len(ids) == 64 == len(set(ids))


def test_fakeout_only_never_uses_the_confluence_path() -> None:
    assert study.FakeoutOnly()._confluence([], [], 1.0) is None


def test_training_eligibility_needs_frequency_and_breadth() -> None:
    assert study.eligible(_row())
    assert not study.eligible(_row(trades=499))
    assert not study.eligible(_row(average_r=0.05))
    assert not study.eligible(_row(side_r={"BUY": 10.0, "SELL": -0.1}))
    assert not study.eligible(_row(quarter_r=[1, 1, 1, 1, -1, -1, -1, -1]))
    assert not study.eligible(_row(market_r={"GOLD": 1.0, "US100": 2.0, "US500": -3.0, "US30": -1.0}))


def test_holdout_pass_requires_training_eligibility_and_every_rule() -> None:
    assert study.holdout_pass(_row(trades=140), True)
    assert not study.holdout_pass(_row(trades=140), False)
    assert not study.holdout_pass(_row(trades=139), True)
    assert not study.holdout_pass(_row(extra_cost_average_r=-0.01), True)
    months = {f"2024-0{m}": (1.0 if m <= 3 else -1.0) for m in range(1, 8)}
    assert not study.holdout_pass(_row(month_r=months), True)


def test_selection_falls_back_to_a_frequent_diagnostic_when_none_is_eligible() -> None:
    rows = [_row(id="rare", trades=100, average_r=0.5), _row(id="busy", trades=800, average_r=-0.01)]
    choice = study.select(rows)
    assert choice["id"] == "busy" and choice["eligible"] is False
