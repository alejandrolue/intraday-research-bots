from __future__ import annotations

from datetime import datetime, timezone

from goldbot.markets import MarketClock, MarketHours

GOLD = {"zone": "UTC", "mon": ["00:00 - 20:59", "22:00 - 00:00"], "tue": ["00:00 - 20:59", "22:00 - 00:00"],
        "wed": ["00:00 - 20:59", "22:00 - 00:00"], "thu": ["00:00 - 20:59", "22:00 - 00:00"],
        "fri": ["00:00 - 20:59"], "sat": [], "sun": ["22:00 - 00:00"]}


def utc(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)   # Sep 28 2026 = Monday


def test_gold_schedule_daily_break_and_weekend() -> None:
    hours = MarketHours(GOLD)
    assert hours.is_open(utc(29, 20, 59))            # Tue 20:59 open ("20:59" is inclusive)
    assert not hours.is_open(utc(29, 21, 0))         # daily break
    assert not hours.is_open(utc(29, 21, 59))
    assert hours.is_open(utc(29, 22, 0))
    assert hours.is_open(utc(29, 23, 59))            # "00:00" end means midnight
    assert not hours.is_open(utc(26, 12, 0))         # Saturday
    assert not hours.is_open(utc(27, 21, 59))        # Sunday before the open
    assert hours.is_open(utc(27, 22, 0))


def test_next_open_after_daily_break_and_over_the_weekend() -> None:
    hours = MarketHours(GOLD)
    assert hours.next_open(utc(29, 21, 30)) == utc(29, 22, 0)
    friday_close = datetime(2026, 10, 2, 21, 0, tzinfo=timezone.utc)
    assert hours.next_open(friday_close) == datetime(2026, 10, 4, 22, 0, tzinfo=timezone.utc)
    assert hours.next_open(utc(29, 12, 0)) == utc(29, 12, 0)   # open now


def test_market_clock_caches_and_treats_unknown_schedules_as_open() -> None:
    calls: list[str] = []

    def details(epic: str) -> dict:
        calls.append(epic)
        return {"instrument": {"openingHours": GOLD}}

    clock = MarketClock(details)
    assert clock.closed_until("GOLD", utc(29, 21, 30)) == utc(29, 22, 0)
    assert clock.closed_until("GOLD", utc(29, 22, 5)) is None
    assert calls == ["GOLD"]                              # cached, not refetched
    assert MarketClock(None).closed_until("GOLD", utc(26, 12, 0)) is None

    def broken(epic: str) -> dict:
        raise RuntimeError("API down")
    assert MarketClock(broken).closed_until("GOLD", utc(26, 12, 0)) is None


def test_watch_makes_no_price_requests_while_the_market_is_closed(tmp_path, monkeypatch) -> None:
    import goldbot.cli as cli

    calls: list[str] = []

    class Client:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def get_market_details(self, epic: str) -> dict:
            calls.append("details")
            return {"instrument": {"openingHours": {"zone": "UTC", **{d: [] for d in
                    ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}}}}

        def get_prices(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            calls.append("prices")
            raise AssertionError("no price requests while closed")

    monkeypatch.setattr(cli, "_capital_client", lambda settings: Client())
    monkeypatch.setattr(cli, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setenv("CAPITAL_EPIC", "GOLD")
    assert cli.main(["watch", "--session", "s", "--paper", "--iterations", "1", "--interval", "10"]) == 0
    assert calls == ["details"]


def test_long_close_is_the_weekend_not_the_daily_break() -> None:
    from datetime import timedelta
    hours = MarketHours(GOLD)
    gap = timedelta(hours=6)
    friday = datetime(2026, 10, 2, 20, 30, tzinfo=timezone.utc)
    assert hours.long_close(friday, min_gap=gap) == datetime(2026, 10, 2, 21, 0, tzinfo=timezone.utc)
    assert hours.long_close(utc(29, 20, 30), min_gap=gap) is None        # Tuesday: daily break only
    assert hours.long_close(utc(27, 23, 0), min_gap=gap) is None         # Sunday night: trades on
    assert hours.long_close(utc(26, 12, 0), min_gap=gap) is None         # Saturday: closed already


def test_weekend_guard_blocks_entries_then_goes_flat(tmp_path) -> None:
    from datetime import timedelta
    from goldbot.cli import _weekend_guard
    from goldbot.markets import MarketClock

    class Runner:
        entry_block = None

        class ledger:  # noqa: N801
            open_positions = [object()]

    class Executor:
        calls: list[str] = []

        def close_all(self, reason: str) -> list:
            self.calls.append(reason)
            return []

    clock = MarketClock(lambda epic: {"instrument": {"openingHours": GOLD}})
    runner, executor = Runner(), Executor()
    friday = datetime(2026, 10, 2, tzinfo=timezone.utc)

    _weekend_guard(clock, "GOLD", friday.replace(hour=19, minute=30), runner, executor, None, enabled=True)
    assert runner.entry_block is None and executor.calls == []            # 90 min before: normal
    _weekend_guard(clock, "GOLD", friday.replace(hour=20, minute=10), runner, executor, None, enabled=True)
    assert "no new entries" in runner.entry_block and executor.calls == []
    _weekend_guard(clock, "GOLD", friday.replace(hour=20, minute=46), runner, executor, None, enabled=True)
    assert executor.calls == ["WEEKEND_CLOSE"]
    _weekend_guard(clock, "GOLD", utc(29, 20, 50), runner, executor, None, enabled=True)
    assert runner.entry_block is None and executor.calls == ["WEEKEND_CLOSE"]   # Tuesday break
    _weekend_guard(clock, "GOLD", friday.replace(hour=20, minute=50), runner, executor, None, enabled=False)
    assert runner.entry_block is None and executor.calls == ["WEEKEND_CLOSE"]   # --hold-over-weekend
