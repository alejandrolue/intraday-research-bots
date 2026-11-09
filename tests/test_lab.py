from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from goldbot.lab import (
    LAB_EPICS,
    LabEntry,
    LabRoster,
    PollCache,
    account_balance,
    load_roster,
    risk_snapshot,
)
from goldbot.models import MarketSetup
from goldbot.paper import PaperLedger
from goldbot.risk import RiskDecision


def write_roster(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "lab.json"
    path.write_text(json.dumps({"account": "lab", "entries": entries}))
    return path


def entry(**changes: object) -> dict:
    base = {"name": "us100-orb15", "epic": "US100", "strategies": ["orb-nycash-15-rr2"]}
    return {**base, **changes}


def test_committed_roster_is_valid() -> None:
    roster = load_roster(Path(__file__).resolve().parents[1] / "lab.json")
    assert roster.account == "YOUR_DEMO_ACCOUNT_ID"
    assert {item.epic for item in roster.entries} == {"US100", "DE40"}
    assert all(not item.manage and item.max_positions == 1 for item in roster.entries)
    assert (roster.max_account_open_risk, roster.max_account_daily_loss) == (0.05, 0.10)


def test_roster_rejects_unknown_markets_strategies_and_duplicates(tmp_path) -> None:
    assert load_roster(write_roster(tmp_path, [entry()])).entries[0].epic == "US100"
    with pytest.raises(ValueError, match="LAB_EPICS"):
        load_roster(write_roster(tmp_path, [entry(epic="BTCUSD")]))
    with pytest.raises(ValueError):
        load_roster(write_roster(tmp_path, [entry(strategies=["orb-nycash-45-rr2"])]))
    with pytest.raises(ValueError, match="unique"):
        load_roster(write_roster(tmp_path, [entry(), entry()]))
    with pytest.raises(ValueError):
        load_roster(write_roster(tmp_path, [entry(hours="asia")]))
    assert "BTCUSD" not in LAB_EPICS


def test_poll_cache_shares_reads_and_invalidates_after_writes() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_positions(self) -> list:
            self.calls.append("positions")
            return []

        def get_prices(self, epic: str, *, resolution: str, max_count: int) -> tuple:
            self.calls.append(f"prices {epic} {resolution}")
            return [], {}

        def get_accounts(self) -> list[dict]:
            self.calls.append("accounts")
            return []

        def create_position(self, **kwargs: object) -> str:
            self.calls.append("create")
            return "o_1"

        def login(self) -> None:
            self.calls.append("login")

    client = Client()
    cache = PollCache(client)
    cache.get_positions(); cache.get_positions()
    cache.get_prices("US100", resolution="MINUTE_5", max_count=500)
    cache.get_prices("US100", resolution="MINUTE_5", max_count=500)
    cache.get_prices("US30", resolution="MINUTE_5", max_count=500)
    cache.get_accounts(); cache.get_accounts()
    assert client.calls == [
        "positions", "prices US100 MINUTE_5", "prices US30 MINUTE_5", "accounts"
    ]

    cache.create_position(epic="US100")
    cache.get_positions()  # a write invalidates positions
    cache.get_prices("US100", resolution="MINUTE_5", max_count=500)  # candles stay cached
    assert client.calls[-2:] == ["create", "positions"]

    cache.new_poll()
    cache.get_prices("US100", resolution="MINUTE_5", max_count=500)
    assert client.calls[-1] == "prices US100 MINUTE_5"
    cache.login()  # everything else passes straight through
    assert client.calls[-1] == "login"


def test_lab_settings_map_every_roster_field() -> None:
    from goldbot.cli import LAB_DIR, _lab_settings, _runner, _settings
    from goldbot.lab import LabRoster

    item = LabEntry(name="j225-orb", epic="J225", strategies=("orb-tokyo-15-rr2",),
                    trend_resolution=None, hours="all", max_spread=20.0, max_trades_per_day=2)
    roster = LabRoster(account="lab", entries=(item,), starting_balance=1000, max_open_positions=40)
    settings = _lab_settings(_settings(), roster, item)

    assert settings.session == "lab-j225-orb"
    assert settings.data_dir == LAB_DIR / "j225-orb"
    assert (settings.capital_epic, settings.capital_trend_resolution) == ("J225", None)
    assert (settings.trading_hours, settings.max_spread, settings.max_trades_per_day) == ("all", 20.0, 2)
    assert settings.manage_trades is False
    runner = _runner(settings, use_ai=False)
    assert runner.trend_filter is None
    assert runner.entry_window is None


def test_minimum_size_floor_only_when_enabled(tmp_path) -> None:
    from goldbot.execution import DemoOrderExecutor
    from goldbot.paper import PaperLedger

    ledger = PaperLedger(tmp_path / "s.json", tmp_path / "j.jsonl", starting_balance=100, point_value_per_unit=1)
    rules = {"minDealSize": {"value": 0.1}, "maxDealSize": {"value": 100}, "minSizeIncrement": {"value": 0.1}}
    strict = DemoOrderExecutor(object(), ledger, tmp_path / "e.jsonl")  # type: ignore[arg-type]
    lab = DemoOrderExecutor(object(), ledger, tmp_path / "e.jsonl", use_minimum_size=True)  # type: ignore[arg-type]
    assert strict._normalized_size(0.02, rules) is None
    assert lab._normalized_size(0.02, rules) == 0.1


def test_one_minute_lab_entries_get_one_minute_strategies_and_their_min_rr(tmp_path) -> None:
    from goldbot.cli import _lab_settings, _runner, _settings

    roster = load_roster(write_roster(tmp_path, [entry(
        name="us500-orbmid30-rr1-1m", epic="US500", strategies=["orbmid-nycash-30-rr1"], resolution="MINUTE",
        trend_resolution=None, max_trades_per_day=1, min_rr=1.0)]))
    lane = roster.entries[0]
    runner = _runner(_lab_settings(_settings(), roster, lane), use_ai=False)

    assert runner.risk_engine.config.minimum_reward_to_risk == 1.0
    assert runner.risk_engine.config.max_trades_per_day == 1
    assert runner.trend_filter is None
    assert [strategy.bar_minutes for strategy in runner.strategies] == [1]
    assert runner.strategies[0].name == "orbmid-nycash-30-rr1"


def test_min_rr_limits(tmp_path) -> None:
    with pytest.raises(ValueError, match="min_rr"):
        load_roster(write_roster(tmp_path, [entry(min_rr=0.5)]))


def test_lab_uses_one_percent_risk_without_the_small_size_cap() -> None:
    from goldbot.cli import _lab_settings, _runner, _settings

    roster = load_roster(Path(__file__).resolve().parents[1] / "lab.json")
    assert (roster.risk_per_trade, roster.max_size, roster.max_daily_loss) == (0.01, 100.0, 0.05)
    settings = _lab_settings(_settings(), roster, roster.entries[0])
    assert settings.capital_max_demo_size == 100.0
    config = _runner(settings, use_ai=False).risk_engine.config
    assert (config.risk_per_trade, config.max_daily_loss) == (0.01, 0.05)


def test_account_balance_reads_the_selected_capital_account() -> None:
    accounts = [
        {"accountId": "other", "balance": {"balance": 500}},
        {"accountId": "lab-id", "balance": {"balance": "1000.25"}},
    ]

    assert account_balance(accounts, "lab-id") == 1000.25
    with pytest.raises(ValueError, match="No single"):
        account_balance(accounts, "missing")
    with pytest.raises(ValueError, match="positive"):
        account_balance([{"accountId": "lab-id", "balance": {"balance": 0}}], "lab-id")


def test_lab_risk_snapshot_survives_restart_and_uses_conservative_close_pnl(tmp_path) -> None:
    ledgers = []
    for name, direction, entry_price, stop, units in (
        ("first", "READY_LONG", 100.0, 98.0, 10.0),
        ("second", "READY_SHORT", 200.0, 205.0, 4.0),
    ):
        directory = tmp_path / name
        ledger = PaperLedger(
            directory / "paper_state.json",
            directory / "journal.jsonl",
            starting_balance=1000,
            point_value_per_unit=1,
        )
        setup = MarketSetup(
            decision=direction,  # type: ignore[arg-type]
            timestamp=datetime(2026, 9, 29, 12, tzinfo=timezone.utc),
            reason="test",
            bias="BULLISH" if direction == "READY_LONG" else "BEARISH",
            entry=entry_price,
            stop=stop,
            target=110.0 if direction == "READY_LONG" else 190.0,
        )
        ledger.open_position(
            setup,
            RiskDecision(True, "approved", units=units, risk_budget=20, expected_reward_to_risk=2),
            deal_id=f"deal-{name}",
        )
        ledgers.append(
            PaperLedger(
                directory / "paper_state.json",
                directory / "journal.jsonl",
                starting_balance=1000,
                point_value_per_unit=1,
            )
        )

    ledgers[0].record_event({
        "event": "PAPER_CLOSE", "timestamp": "2026-09-29T13:00:00+00:00", "broker_pnl": -80,
    })
    ledgers[0].record_event({
        "event": "PAPER_CLOSE", "timestamp": "2026-09-28T13:00:00+00:00", "broker_pnl": 999,
    })
    ledgers[1].record_event({
        "event": "PAPER_CLOSE", "timestamp": "2026-09-29T13:00:00+00:00", "pnl": -30,
    })
    ledgers[1].record_event({
        "event": "PAPER_CLOSE", "timestamp": "2026-09-29T14:00:00+00:00", "pnl": 500,
    })

    snapshot = risk_snapshot(
        ledgers,
        account_balance_value=1000,
        trading_day=date(2026, 9, 29),
        max_open_risk=0.05,
        max_daily_loss=0.10,
    )

    assert snapshot.open_risk == 40
    assert snapshot.open_risk_limit == 50
    assert snapshot.remaining_open_risk == 10
    assert snapshot.broker_pnl_today == -80
    assert snapshot.estimated_pnl_today == -30
    assert snapshot.account_pnl_today == -110
    assert snapshot.daily_loss_limit == 100
    assert snapshot.daily_loss_blocked is True


def test_lab_open_risk_ignores_stops_that_protect_entry(tmp_path) -> None:
    ledger = PaperLedger(
        tmp_path / "paper_state.json",
        tmp_path / "journal.jsonl",
        starting_balance=1000,
        point_value_per_unit=1,
    )
    setup = MarketSetup(
        decision="READY_LONG",
        timestamp=datetime(2026, 9, 29, 12, tzinfo=timezone.utc),
        reason="test",
        bias="BULLISH",
        entry=100,
        stop=98,
        target=104,
    )
    opened = ledger.open_position(
        setup,
        RiskDecision(True, "approved", units=10, risk_budget=20, expected_reward_to_risk=2),
        deal_id="deal-protected",
    )
    ledger.update_levels(
        opened["position"]["local_id"],
        stop=101,
        target=104,
        timestamp="2026-09-29T12:05:00+00:00",
        reason="TRAILING_STOP",
    )

    snapshot = risk_snapshot(
        [PaperLedger(
            ledger.state_path,
            ledger.journal_path,
            starting_balance=1000,
            point_value_per_unit=1,
        )],
        account_balance_value=1000,
        trading_day=date(2026, 9, 29),
        max_open_risk=0.05,
        max_daily_loss=0.10,
    )

    assert snapshot.open_risk == 0
    assert snapshot.remaining_open_risk == 50


class _LabClient:
    def __init__(self, accounts: list[dict]) -> None:
        self.accounts = accounts
        self.account_calls = 0

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def use_account(self, _: str) -> dict:
        return {"accountId": "lab-id", "accountName": "lab"}

    def get_accounts(self) -> list[dict]:
        self.account_calls += 1
        return self.accounts


class _LabClock:
    def __init__(self, *, closed: bool, weekend: bool) -> None:
        self.closed = closed
        self.weekend = weekend

    def closed_until(self, _: str, now: datetime) -> datetime | None:
        return now + timedelta(hours=1) if self.closed else None

    def weekend_close(self, _: str, now: datetime) -> datetime | None:
        return now + timedelta(minutes=10) if self.weekend else None


class _LabExecutor:
    def __init__(self, ledger: PaperLedger) -> None:
        self.ledger = ledger
        self.risk_budgets: list[float | None] = []
        self.reconcile_calls = 0
        self.manage_calls = 0
        self.close_calls = 0

    def reconcile(self) -> list[dict]:
        self.reconcile_calls += 1
        return []

    def manage(self, *, allow_add: bool, max_risk_budget: float | None) -> list[dict]:
        assert allow_add is False
        assert max_risk_budget is None
        self.manage_calls += 1
        return []

    def execute(
        self,
        setup: MarketSetup,
        risk: RiskDecision,
        *,
        epic: str,
        max_risk_budget: float | None = None,
        context: dict | None = None,
    ) -> object:
        from goldbot.execution import DemoExecutionResult

        self.risk_budgets.append(max_risk_budget)
        event = self.ledger.open_position(
            setup,
            risk,
            deal_id=f"deal-{len(self.risk_budgets)}",
        )
        return DemoExecutionResult(
            "OPENED",
            "opened",
            epic,
            strategy=setup.strategy,
            size=risk.units,
            deal_id=event["position"]["deal_id"],
            paper_event=event,
        )

    def close_all(self, reason: str) -> list[dict]:
        self.close_calls += 1
        events = []
        for position in list(self.ledger.open_positions):
            events.append(self.ledger.close_position(
                position.local_id,
                exit_price=position.entry,
                exit_reason=reason,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ))
        return events


def _run_lab_poll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    entries: int = 1,
    accounts: list[dict] | None = None,
    closed: bool = False,
    weekend: bool = False,
    preopen: bool = False,
    manage: bool = False,
) -> tuple[_LabClient, list[object], list[_LabExecutor]]:
    import goldbot.cli as cli

    roster_entries = tuple(
        LabEntry(
            name=f"lane-{index}",
            epic="US100",
            strategies=("orb-nycash-15-rr2",),
            trend_resolution=None,
            manage=manage,
        )
        for index in range(entries)
    )
    roster = LabRoster(account="lab", entries=roster_entries)
    client = _LabClient(
        [{"accountId": "lab-id", "balance": {"balance": 1000}}]
        if accounts is None
        else accounts
    )
    runners: list[object] = []
    executors: list[_LabExecutor] = []

    def make_runner(settings: object, *, use_ai: bool) -> object:
        del use_ai
        data_dir = settings.data_dir  # type: ignore[attr-defined]
        ledger = PaperLedger(
            data_dir / "paper_state.json",
            data_dir / "journal.jsonl",
            starting_balance=1000,
            point_value_per_unit=1,
        )
        runner = SimpleNamespace(ledger=ledger, entry_block=None)
        if preopen:
            setup = MarketSetup(
                decision="READY_LONG",
                timestamp=datetime(2026, 9, 29, 12, tzinfo=timezone.utc),
                reason="existing",
                bias="BULLISH",
                entry=100,
                stop=98,
                target=104,
            )
            ledger.open_position(
                setup,
                RiskDecision(True, "approved", units=10, risk_budget=20,
                             expected_reward_to_risk=2),
                deal_id="existing",
            )
        runners.append(runner)
        return runner

    def make_executor(settings: object, cache: object, runner: object, **_: object) -> _LabExecutor:
        del settings, cache
        executor = _LabExecutor(runner.ledger)  # type: ignore[attr-defined]
        executors.append(executor)
        return executor

    signal_index = 0

    def run_once(*_: object, **__: object) -> tuple[str, object]:
        nonlocal signal_index
        signal_index += 1
        timestamp = datetime(2026, 9, 29, 12, signal_index, tzinfo=timezone.utc)
        setup = MarketSetup(
            decision="READY_LONG",
            timestamp=timestamp,
            reason="test",
            bias="BULLISH",
            strategy=f"lane-{signal_index}",
            entry=100,
            stop=98,
            target=104,
        )
        risk = RiskDecision(
            True,
            "approved",
            units=10,
            risk_budget=20,
            expected_reward_to_risk=2,
        )
        return timestamp.isoformat(), SimpleNamespace(
            paper_entry_approved=True,
            setup=setup,
            risk=risk,
        )

    monkeypatch.setattr(cli, "LAB_DIR", tmp_path / "lab")
    monkeypatch.setattr(cli, "load_roster", lambda _: roster)
    monkeypatch.setattr(cli, "_capital_client", lambda _: client)
    monkeypatch.setattr(cli, "MarketClock", lambda _: _LabClock(closed=closed, weekend=weekend))
    monkeypatch.setattr(cli, "_runner", make_runner)
    monkeypatch.setattr(cli, "_demo_executor", make_executor)
    monkeypatch.setattr(cli, "_run_once", run_once)
    monkeypatch.setattr(cli, "write_session_info", lambda *_: None)
    monkeypatch.setattr(cli, "_print", lambda *_: None)

    settings = replace(
        cli._settings(),
        capital_demo_execution_enabled=True,
        telegram_bot_token=None,
        telegram_chat_id=None,
        telegram_allowed_user_id=None,
    )
    args = SimpleNamespace(roster=tmp_path / "lab.json", check=False, iterations=1)
    assert cli._lab_command(args, settings) == 0
    return client, runners, executors


def test_lab_poll_sequences_same_poll_entries_against_updated_open_risk(
    tmp_path, monkeypatch
) -> None:
    client, _, executors = _run_lab_poll(tmp_path, monkeypatch, entries=2)

    assert client.account_calls == 1
    assert [executor.risk_budgets for executor in executors] == [[50], [30]]


def test_lab_poll_skips_balance_request_when_all_markets_are_closed(
    tmp_path, monkeypatch
) -> None:
    client, runners, executors = _run_lab_poll(
        tmp_path,
        monkeypatch,
        entries=2,
        closed=True,
    )

    assert client.account_calls == 0
    assert all(not runner.ledger.open_positions for runner in runners)  # type: ignore[attr-defined]
    assert all(not executor.risk_budgets for executor in executors)


def test_lab_poll_balance_failure_blocks_entries_but_keeps_closing_paths(
    tmp_path, monkeypatch
) -> None:
    client, runners, executors = _run_lab_poll(
        tmp_path,
        monkeypatch,
        accounts=[],
        weekend=True,
        preopen=True,
        manage=True,
    )

    assert client.account_calls == 1
    assert executors[0].close_calls == 1
    assert executors[0].reconcile_calls == 1
    assert executors[0].manage_calls == 1
    assert executors[0].risk_budgets == []
    events = [json.loads(line) for line in runners[0].ledger.journal_path.read_text().splitlines()]  # type: ignore[attr-defined]
    assert any(event["event"] == "ACCOUNT_RISK_BLOCK" for event in events)


