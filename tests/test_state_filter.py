from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "research" / "state_filter_study.py"


@pytest.fixture(scope="module")
def study():
    data = Path(__file__).resolve().parents[1] / "data" / "history" / "GOLD" / "MINUTE_5"
    if not data.exists():
        pytest.skip("needs the local GOLD history cache")
    spec = importlib.util.spec_from_file_location("state_filter_study", PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["state_filter_study"] = module
    spec.loader.exec_module(module)
    return module


def regime(**overrides):
    base = {"label": "trend_down", "adx": 31.0, "volatility_pct": 0.8, "efficiency": 0.55,
            "position_in_range": 0.1, "nearest_open": "london"}
    base.update(overrides)
    return base


def test_tags_read_the_state_relative_to_the_trade(study) -> None:
    sell = study.tags(regime(), "SELL")
    buy = study.tags(regime(), "BUY")
    assert sell["state"] == "trend_with" and buy["state"] == "trend_against"
    assert sell["range"] == "late" and buy["range"] == "early"
    assert (sell["adx"], sell["volatility"], sell["efficiency"]) == (">=30", ">=0.7", ">=0.5")
    assert study.tags(regime(label="sideways", position_in_range=0.5), "BUY")["state"] == "sideways"
    assert study.tags({}, "BUY") is None


def test_allowed_needs_every_dimension(study) -> None:
    row = {"tags": study.tags(regime(), "SELL")}
    assert study.allowed(row, {"session": ["london"], "adx": [">=30"]})
    assert not study.allowed(row, {"session": ["london"], "adx": ["<20"]})
    assert not study.allowed({"tags": None}, {"session": ["london"]})
