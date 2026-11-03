from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from goldbot.cli import _run_once
from goldbot.models import Candle


class FakeClient:
    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles

    def get_prices(self, *_: object, **__: object) -> tuple[list[Candle], dict[str, object]]:
        return self.candles, {}


class FakeRunner:
    def __init__(self) -> None:
        self.calls = 0

    def process(self, *_: object, **__: object) -> object:
        self.calls += 1
        raise AssertionError("Duplicate candle should have been skipped before processing")


def test_run_once_skips_an_already_seen_completed_candle() -> None:
    start = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    candles = [
        Candle(start + timedelta(minutes=5 * index), 100, 101, 99, 100)
        for index in range(52)
    ]
    completed_timestamp = candles[-2].timestamp.isoformat()
    runner = FakeRunner()

    timestamp, result = _run_once(
        FakeClient(candles),  # type: ignore[arg-type]
        SimpleNamespace(capital_epic="GOLD"),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        use_ai=False,
        paper=True,
        skip_timestamp=completed_timestamp,
    )

    assert timestamp == completed_timestamp
    assert result is None
    assert runner.calls == 0


def test_stats_summarizes_closed_trades_per_session(tmp_path) -> None:
    from goldbot.stats import format_summary, summarize

    session = tmp_path / "1h"
    session.mkdir()
    position = {"direction": "BUY", "entry": 100.0, "stop": 98.0, "target": 103.0, "units": 1.0}
    events = [
        {
            "event": "PAPER_CLOSE",
            "pnl": 3.0,
            "broker_pnl": 2.75,
            "broker_currency": "CHF",
            "position": {**position, "strategy": "ema_pullback"},
        },
        {
            "event": "PAPER_CLOSE",
            "pnl": -2.0,
            "broker_pnl": -1.25,
            "broker_currency": "CHF",
            "position": {**position, "strategy": "ema_pullback"},
        },
        {"event": "DECISION"},
    ]
    (session / "journal.jsonl").write_text("\n".join(json.dumps(event) for event in events) + "\n")

    summary = summarize(session)

    assert summary["trades"] == 2
    assert summary["win_rate"] == 0.5
    assert summary["net_pnl"] == 1.0
    assert summary["average_r"] == 0.25
    assert summary["profit_factor"] == 1.5
    assert summary["broker_pnl"]["CHF"]["net_pnl"] == 1.5
    text = format_summary(summary)
    assert "Local W/L: 1/1 (50% win rate)" in text
    assert "Broker realised: CHF +1.50 (W/L 1/1, 2/2 trades matched)" in text
    assert "Local estimate: USD +1.00" in text


def test_stats_keeps_older_local_only_journals_readable(tmp_path) -> None:
    from goldbot.stats import format_summary, summarize

    session = tmp_path / "legacy"
    session.mkdir()
    event = {
        "event": "PAPER_CLOSE",
        "pnl": 1.0,
        "position": {
            "direction": "BUY",
            "entry": 100.0,
            "stop": 99.0,
            "target": 102.0,
            "units": 1.0,
        },
    }
    (session / "journal.jsonl").write_text(json.dumps(event) + "\n")

    summary = summarize(session)

    assert summary["broker_pnl"] == {}
    assert "Broker realised: unavailable (0/1 matched)" in format_summary(summary)


def test_session_options_isolate_data_and_override_resolutions() -> None:
    from goldbot.cli import SESSIONS_DIR, _parser, _settings

    args = _parser().parse_args(
        ["watch", "--session", "1h", "--resolution", "HOUR", "--trend-resolution", "HOUR_4",
         "--starting-balance", "1000"]
    )
    settings = _settings(args)

    assert settings.data_dir == SESSIONS_DIR / "1h"
    assert settings.session == "1h"
    assert settings.capital_resolution == "HOUR"
    assert settings.capital_trend_resolution == "HOUR_4"
    assert settings.paper_starting_balance == 1000


def test_confirm_trend_option_sets_and_disables_the_confirm_filter() -> None:
    import pytest

    from goldbot.cli import _parser, _settings

    enabled = _settings(
        _parser().parse_args(["watch", "--session", "1h", "--confirm-trend-resolution", "DAY"])
    )
    disabled = _settings(
        _parser().parse_args(["watch", "--session", "1h", "--confirm-trend-resolution", "none"])
    )

    assert enabled.capital_confirm_resolution == "DAY"
    assert disabled.capital_confirm_resolution is None
    with pytest.raises(ValueError):
        _settings(_parser().parse_args(["watch", "--confirm-trend-resolution", "DAILY"]))


def test_restart_does_not_reprocess_the_last_candle(tmp_path, monkeypatch) -> None:
    """Regression: a restart re-traded the candle it had already acted on (duplicate entry)."""
    import goldbot.cli as cli

    start = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    candles = [
        Candle(start + timedelta(minutes=5 * i), 100 + i * 0.01, 100.5 + i * 0.01, 99.5 + i * 0.01,
               100 + i * 0.01, spread=0.3)
        for i in range(600)
    ]

    class Client:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def get_prices(self, epic: str, *, resolution: str, max_count: int):  # type: ignore[no-untyped-def]
            return candles[-max_count:], {}

    monkeypatch.setattr(cli, "_capital_client", lambda settings: Client())
    monkeypatch.setattr(cli, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setenv("CAPITAL_EPIC", "GOLD")
    args = ["watch", "--session", "s", "--paper", "--iterations", "1", "--interval", "10"]

    assert cli.main(args) == 0
    journal = tmp_path / "sessions" / "s" / "journal.jsonl"
    state = json.loads((tmp_path / "sessions" / "s" / "paper_state.json").read_text())
    assert state["last_processed_timestamp"] == candles[-2].timestamp.isoformat()
    decisions = journal.read_text().count('"event":"DECISION"')
    assert decisions == 1

    assert cli.main(args) == 0  # "restart" on the same candles
    assert journal.read_text().count('"event":"DECISION"') == decisions


def test_telegram_only_hears_about_rejections_not_routine_blocks() -> None:
    from goldbot.cli import _parser, _worth_notifying

    assert _worth_notifying("REJECTED")
    assert not _worth_notifying("BLOCKED")
    assert not _parser().parse_args(["watch"]).telegram_decisions
    assert _parser().parse_args(["watch", "--telegram-decisions"]).telegram_decisions
