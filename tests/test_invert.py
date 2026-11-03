from __future__ import annotations

from datetime import datetime, timezone

import pytest

from goldbot.cli import _parser, _settings
from goldbot.invert import invert_setup, original_reward_ok
from goldbot.models import MarketSetup

LONG = MarketSetup("READY_LONG", datetime(2026, 10, 1, tzinfo=timezone.utc), "x", "BULLISH",
                   "ema_pullback", entry=100.0, stop=98.0, target=103.0)


def test_mirror_flips_direction_with_the_same_geometry() -> None:
    short = invert_setup(LONG, "mirror")
    assert (short.decision, short.bias) == ("READY_SHORT", "BEARISH")
    assert (short.entry, short.stop, short.target) == (100.0, 102.0, 97.0)
    back = invert_setup(short, "mirror")
    assert (back.decision, back.stop, back.target) == ("READY_LONG", 98.0, 103.0)


def test_exact_swaps_stop_and_target() -> None:
    short = invert_setup(LONG, "exact")
    assert (short.decision, short.stop, short.target) == ("READY_SHORT", 103.0, 98.0)


def test_trend_keep_uses_mirror_geometry_and_no_trade_is_untouched() -> None:
    assert invert_setup(LONG, "trend-keep").stop == 102.0
    waiting = MarketSetup("NO_TRADE", LONG.timestamp, "none", "NEUTRAL")
    assert invert_setup(waiting, "exact") is waiting
    with pytest.raises(ValueError):
        invert_setup(LONG, "sideways")


def test_original_reward_gate() -> None:
    assert original_reward_ok(LONG, 1.5)
    assert not original_reward_ok(LONG, 1.6)


def test_cli_option_is_off_by_default() -> None:
    assert _settings(_parser().parse_args(["watch"])).invert is None
    assert _settings(_parser().parse_args(["watch", "--invert", "exact"])).invert == "exact"
