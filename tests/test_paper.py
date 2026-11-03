from __future__ import annotations

from datetime import datetime, timedelta, timezone

from goldbot.models import Candle, MarketSetup
from goldbot.paper import PaperLedger
from goldbot.risk import RiskDecision


NOW = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)


def test_paper_ledger_uses_stop_when_stop_and_target_touch_same_candle(tmp_path) -> None:
    ledger = PaperLedger(
        tmp_path / "state.json",
        tmp_path / "journal.jsonl",
        starting_balance=10_000,
        point_value_per_unit=1.0,
    )
    setup = MarketSetup(
        decision="READY_LONG",
        timestamp=NOW,
        reason="test",
        bias="BULLISH",
        entry=100,
        stop=98,
        target=104,
    )
    risk = RiskDecision(True, "approved", units=10, risk_budget=20, expected_reward_to_risk=2)
    ledger.open_position(setup, risk)
    candle = Candle(NOW + timedelta(minutes=5), 100, 105, 97, 101)

    events = ledger.update_open_positions(candle)

    assert len(events) == 1
    event = events[0]
    assert event["exit_reason"] == "STOP"
    assert event["pnl"] == -20
    assert ledger.state.balance == 9_980
    assert ledger.open_positions == []


def test_paper_ledger_leaves_broker_managed_positions_to_reconciliation(tmp_path) -> None:
    ledger = PaperLedger(
        tmp_path / "state.json",
        tmp_path / "journal.jsonl",
        starting_balance=10_000,
        point_value_per_unit=1.0,
    )
    setup = MarketSetup(
        decision="READY_LONG",
        timestamp=NOW,
        reason="test",
        bias="BULLISH",
        entry=100,
        stop=98,
        target=104,
    )
    risk = RiskDecision(True, "approved", units=1, risk_budget=2, expected_reward_to_risk=2)
    ledger.open_position(setup, risk, deal_id="pos-1")

    assert ledger.update_open_positions(Candle(NOW + timedelta(minutes=5), 100, 105, 97, 101)) == []
    assert len(ledger.open_positions) == 1


def test_paper_ledger_loads_legacy_single_position_state(tmp_path) -> None:
    import json

    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "balance": 10_000,
                "daily_realized_pnl": 0,
                "trades_today": 1,
                "trading_day": "2026-09-28",
                "open_position": {
                    "direction": "BUY",
                    "entry": 100,
                    "stop": 98,
                    "target": 104,
                    "units": 1,
                    "opened_at": NOW.isoformat(),
                },
            }
        )
    )

    ledger = PaperLedger(
        state, tmp_path / "journal.jsonl", starting_balance=10_000, point_value_per_unit=1.0
    )

    assert len(ledger.open_positions) == 1
    assert ledger.open_positions[0].initial_stop == 98


def test_risk_state_reports_current_open_stop_risk(tmp_path) -> None:
    ledger = PaperLedger(
        tmp_path / "state.json",
        tmp_path / "journal.jsonl",
        starting_balance=10_000,
        point_value_per_unit=2.0,
    )
    setup = MarketSetup(
        "READY_LONG", NOW, "test", "BULLISH",
        entry=100, stop=98, target=104,
    )
    risk = RiskDecision(True, "approved", units=10, risk_budget=40, expected_reward_to_risk=2)
    ledger.open_position(setup, risk)

    assert ledger.risk_state(NOW).open_risk == 40
    ledger.update_levels(
        ledger.open_positions[0].local_id,
        stop=101,
        target=104,
        timestamp=NOW.isoformat(),
        reason="protected",
    )
    assert ledger.risk_state(NOW).open_risk == 0


def test_stats_report_overnight_fees_and_net(tmp_path) -> None:
    import json as _json

    from goldbot.stats import format_summary, summarize

    position = {"direction": "BUY", "entry": 100.0, "stop": 98.0, "initial_stop": 98.0,
                "target": 104.0, "units": 1.0, "strategy": "s", "opened_at": "2026-10-01T00:00:00"}
    rows = [
        {"event": "PAPER_CLOSE", "timestamp": "2026-10-02T00:00:00", "exit_price": 104.0,
         "pnl": 4.0, "broker_pnl": 3.2, "broker_currency": "CHF", "position": position},
        {"event": "FINANCING", "timestamp": "2026-10-01T21:00:00", "amount": -0.8, "currency": "CHF",
         "reference": "f1"},
    ]
    (tmp_path / "journal.jsonl").write_text("\n".join(_json.dumps(row) for row in rows) + "\n")
    summary = summarize(tmp_path)
    assert summary["financing"] == {"CHF": -0.8}
    assert "Overnight fees: CHF -0.80 (net after fees CHF +2.40)" in format_summary(summary)
