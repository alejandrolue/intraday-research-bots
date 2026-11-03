from __future__ import annotations

from datetime import datetime, timezone

from goldbot.sessions import TradingWindow, trading_window


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_window_follows_london_and_new_york_daylight_saving() -> None:
    window = TradingWindow()
    # Summer (BST/EDT): London 08:00 = 07:00 UTC, New York 16:00 = 20:00 UTC.
    assert window.block_reason(utc(2026, 7, 1, 6, 59)) is not None
    assert window.block_reason(utc(2026, 7, 1, 7, 20)) is None
    assert window.block_reason(utc(2026, 7, 1, 19, 55)) is None
    assert window.block_reason(utc(2026, 7, 1, 20, 0)) is not None
    # Winter (GMT/EST): London 08:00 = 08:00 UTC, New York 16:00 = 21:00 UTC.
    assert window.block_reason(utc(2026, 1, 14, 7, 30)) is not None
    assert window.block_reason(utc(2026, 1, 14, 20, 30)) is None


def test_window_guards_the_london_open_and_us_data_release() -> None:
    window = TradingWindow(guard_minutes=15)
    assert "London open" in (window.block_reason(utc(2026, 7, 1, 7, 10)) or "")
    assert window.block_reason(utc(2026, 7, 1, 7, 15)) is None
    assert "08:30 ET" in (window.block_reason(utc(2026, 7, 1, 12, 40)) or "")
    assert window.block_reason(utc(2026, 7, 1, 12, 45)) is None


def test_window_blocks_weekends_and_all_disables_it() -> None:
    assert "Weekend" in (TradingWindow().block_reason(utc(2026, 9, 26, 12, 0)) or "")
    assert trading_window("all") is None
