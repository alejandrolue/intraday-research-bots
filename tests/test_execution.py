from __future__ import annotations

import json

import pytest
from datetime import datetime, timezone

from goldbot.capital import CapitalAPIError
from goldbot.execution import DemoOrderExecutor
from goldbot.models import MarketSetup
from goldbot.paper import PaperLedger
from goldbot.risk import RiskDecision


class FakeClient:
    def __init__(
        self,
        *,
        positions: list[dict[str, object]] | None = None,
        activities: list[dict[str, object]] | None = None,
        transactions: list[dict[str, object]] | None = None,
        offer: float = 100.1,
    ) -> None:
        self.positions = positions or []
        self.activities = activities or []
        self.transactions = transactions or []
        self.transaction_errors = 0
        self.offer = offer
        self.created: list[dict[str, object]] = []
        self.updated: list[dict[str, object]] = []

    def get_activity(self) -> list[dict[str, object]]:
        return self.activities

    def get_transactions(self, transaction_type: str | None = None, **_: object) -> list[dict[str, object]]:
        if transaction_type == "SWAP":
            return [t for t in self.transactions if t.get("transactionType") == "SWAP"]
        if self.transaction_errors:
            self.transaction_errors -= 1
            raise CapitalAPIError("temporary transaction-history failure")
        return self.transactions

    def get_positions(self) -> list[dict[str, object]]:
        return self.positions

    def get_market_details(self, epic: str) -> dict[str, object]:
        assert epic == "GOLD"
        return {
            "snapshot": {
                "marketStatus": "TRADEABLE",
                "bid": round(self.offer - 0.2, 2),
                "offer": self.offer,
                "decimalPlacesFactor": 2,
            },
            "dealingRules": {
                "minDealSize": {"unit": "POINTS", "value": 0.1},
                "maxDealSize": {"unit": "POINTS", "value": 100},
                "minSizeIncrement": {"unit": "POINTS", "value": 0.1},
                "minStopOrProfitDistance": {"unit": "PERCENTAGE", "value": 0.01},
                "maxStopOrProfitDistance": {"unit": "PERCENTAGE", "value": 60},
            },
        }

    def create_position(self, **payload: object) -> str:
        self.created.append(payload)
        return f"o_{len(self.created)}"

    def update_position(self, deal_id: str, **payload: object) -> str:
        self.updated.append({"deal_id": deal_id, **payload})
        return f"p_{deal_id}"

    def get_confirmation(self, deal_reference: str) -> dict[str, object]:
        if deal_reference.startswith("p_"):
            return {"dealStatus": "ACCEPTED", "status": "AMENDED"}
        number = deal_reference.removeprefix("o_")
        return {
            "dealStatus": "ACCEPTED",
            "dealId": f"order-{number}",
            "affectedDeals": [{"dealId": f"pos-{number}", "status": "OPENED"}],
            "level": self.offer,
        }


def ready_setup() -> MarketSetup:
    return MarketSetup(
        decision="READY_LONG",
        timestamp=datetime(2026, 9, 28, tzinfo=timezone.utc),
        reason="test",
        bias="BULLISH",
        entry=100,
        stop=98,
        target=104,
    )


def ledger(tmp_path) -> PaperLedger:
    return PaperLedger(
        tmp_path / "paper.json",
        tmp_path / "journal.jsonl",
        starting_balance=10_000,
        point_value_per_unit=1,
    )


RISK = RiskDecision(True, "approved", units=1, risk_budget=2, expected_reward_to_risk=2)


def executor_for(tmp_path, client: FakeClient, **kwargs: object) -> tuple[DemoOrderExecutor, PaperLedger]:
    paper = ledger(tmp_path)
    executor = DemoOrderExecutor(
        client,  # type: ignore[arg-type]
        paper,
        tmp_path / "execution.jsonl",
        **kwargs,  # type: ignore[arg-type]
    )
    return executor, paper


def broker(deal_id: str, direction: str = "BUY") -> dict[str, object]:
    return {"position": {"dealId": deal_id, "direction": direction}, "market": {"epic": "GOLD"}}


def test_demo_executor_caps_size_and_mirrors_accepted_position(tmp_path) -> None:
    client = FakeClient()
    executor, paper = executor_for(tmp_path, client, max_size=1)
    risk = RiskDecision(True, "approved", units=25, risk_budget=50, expected_reward_to_risk=2)

    result = executor.execute(ready_setup(), risk, epic="GOLD")

    assert result.status == "OPENED"
    assert result.size == 1
    assert result.deal_id == "pos-1"
    assert client.created == [
        {"epic": "GOLD", "direction": "BUY", "size": 1.0, "stop_level": 98.1, "profit_level": 104.1}
    ]
    assert len(paper.open_positions) == 1
    assert paper.open_positions[0].units == 1
    assert paper.open_positions[0].deal_id == "pos-1"


def test_demo_executor_scales_size_to_remaining_account_risk(tmp_path) -> None:
    client = FakeClient()
    executor, paper = executor_for(tmp_path, client, max_size=100, use_minimum_size=True)

    result = executor.execute(ready_setup(), RISK, epic="GOLD", max_risk_budget=1.2)

    assert result.status == "OPENED"
    assert result.size == 0.6
    assert client.created[0]["size"] == 0.6
    events = [json.loads(line) for line in paper.journal_path.read_text().splitlines()]
    scaled = next(event for event in events if event["event"] == "ACCOUNT_RISK_SCALED")
    assert scaled["requested_size"] == 1
    assert scaled["requested_risk"] == 2
    assert scaled["remaining_risk_budget"] == 1.2
    assert scaled["size"] == 0.6
    assert scaled["scaled_risk"] == 1.2


def test_demo_executor_blocks_when_minimum_size_exceeds_remaining_risk(tmp_path) -> None:
    client = FakeClient()
    executor, paper = executor_for(tmp_path, client, max_size=100, use_minimum_size=True)

    result = executor.execute(ready_setup(), RISK, epic="GOLD", max_risk_budget=0.1)

    assert result.status == "BLOCKED"
    assert "minimum size" in result.reason
    assert client.created == []
    events = [json.loads(line) for line in paper.journal_path.read_text().splitlines()]
    blocked = next(event for event in events if event["event"] == "ACCOUNT_RISK_BLOCK")
    assert blocked["requested_risk"] == 2
    assert blocked["remaining_risk_budget"] == 0.1


def test_demo_executor_respects_account_wide_position_cap(tmp_path) -> None:
    client = FakeClient(positions=[broker("a"), broker("b")])
    executor, _ = executor_for(tmp_path, client, hedging_mode=True, max_open_positions=2)

    result = executor.execute(ready_setup(), RISK, epic="GOLD")

    assert result.status == "BLOCKED"
    assert "cap is 2" in result.reason
    assert client.created == []


def test_demo_executor_allows_another_sessions_position_when_hedging(tmp_path) -> None:
    client = FakeClient(positions=[broker("a", "SELL")])
    executor, _ = executor_for(tmp_path, client, hedging_mode=True)

    assert executor.execute(ready_setup(), RISK, epic="GOLD").status == "OPENED"


def test_demo_executor_blocks_opposite_position_without_hedging(tmp_path) -> None:
    client = FakeClient(positions=[broker("a", "SELL")])
    executor, _ = executor_for(tmp_path, client)

    result = executor.execute(ready_setup(), RISK, epic="GOLD")

    assert result.status == "BLOCKED"
    assert "Hedging mode is off" in result.reason
    assert client.created == []


def test_demo_executor_stacks_same_direction_signals(tmp_path) -> None:
    client = FakeClient()
    executor, paper = executor_for(tmp_path, client, hedging_mode=True)

    assert executor.execute(ready_setup(), RISK, epic="GOLD").status == "OPENED"
    client.positions = [broker("pos-1")]
    assert executor.execute(ready_setup(), RISK, epic="GOLD").status == "OPENED"

    assert len(paper.open_positions) == 2


def test_demo_executor_shifts_levels_to_preserve_planned_reward_to_risk(tmp_path) -> None:
    client = FakeClient(offer=100.8)
    executor, _ = executor_for(tmp_path, client)

    result = executor.execute(ready_setup(), RISK, epic="GOLD")

    assert result.status == "OPENED"
    assert client.created[0]["stop_level"] == 98.8
    assert client.created[0]["profit_level"] == 104.8


def test_demo_executor_refuses_to_chase_a_signal_that_moved_too_far(tmp_path) -> None:
    client = FakeClient(offer=101.5)
    executor, _ = executor_for(tmp_path, client)

    result = executor.execute(ready_setup(), RISK, epic="GOLD")

    assert result.status == "BLOCKED"
    assert "moved" in result.reason
    assert client.created == []


def opened(tmp_path, client: FakeClient, **kwargs: object) -> tuple[DemoOrderExecutor, PaperLedger]:
    # Entry 100.1, stop 98.1 (1R = 2.0 points), target 104.1.
    executor, paper = executor_for(tmp_path, client, hedging_mode=True, **kwargs)
    assert executor.execute(ready_setup(), RISK, epic="GOLD").status == "OPENED"
    client.positions = [broker("pos-1")]
    return executor, paper


def test_manage_leaves_levels_alone_below_one_r(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client)
    client.offer = 102.0  # bid 101.8 = +0.85R

    assert executor.manage(allow_add=True) == []
    assert client.updated == []
    assert paper.open_positions[0].stop == 98.1


def test_manage_moves_stop_to_breakeven_at_one_r(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client)
    client.offer = 102.3  # bid 102.1 = +1R

    events = executor.manage(allow_add=False)

    assert [event["reason"] for event in events] == ["BREAKEVEN"]
    assert events[0]["notify"] is True
    assert client.updated == [{"deal_id": "pos-1", "stop_level": 100.1, "profit_level": 104.1}]
    assert paper.open_positions[0].stop == 100.1
    assert paper.open_positions[0].initial_stop == 98.1


def test_manage_trails_stop_and_only_ratchets_forward(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client)
    client.offer = 103.3  # bid 103.1 = +1.5R
    executor.manage(allow_add=False)
    assert paper.open_positions[0].stop == 101.1

    client.offer = 102.8  # pulls back; the stop must not loosen
    assert executor.manage(allow_add=False) == []
    assert paper.open_positions[0].stop == 101.1


def test_manage_extends_target_when_price_nears_it(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client)
    client.offer = 103.9  # bid 103.7, within 0.25R of the 104.1 target

    events = executor.manage(allow_add=False)

    assert events[0]["reason"] == "BREAKEVEN+TARGET_EXTENDED"
    assert paper.open_positions[0].target == 106.1
    assert paper.open_positions[0].stop == 101.7


def test_manage_adds_to_a_winner_with_stop_at_protected_level(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client)
    client.offer = 102.3  # +1R: stop to breakeven, then add

    events = executor.manage(allow_add=True)

    assert [event["event"] for event in events] == ["LEVELS_UPDATED", "PAPER_OPEN"]
    assert len(paper.open_positions) == 2
    added = paper.open_positions[1]
    assert added.role == "add"
    assert added.stop == 100.1
    assert added.target == 104.1
    assert client.created[1]["direction"] == "BUY"


def test_manage_does_not_add_when_account_risk_cannot_fit_minimum_size(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client, use_minimum_size=True)
    client.offer = 102.3

    events = executor.manage(allow_add=True, max_risk_budget=0.1)

    assert [event["event"] for event in events] == ["LEVELS_UPDATED"]
    assert len(paper.open_positions) == 1
    assert len(client.created) == 1
    journal = [json.loads(line) for line in paper.journal_path.read_text().splitlines()]
    assert journal[-1]["event"] == "ACCOUNT_RISK_BLOCK"


def test_manage_does_not_add_when_not_allowed(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client)
    client.offer = 102.3

    executor.manage(allow_add=False)

    assert len(paper.open_positions) == 1


def test_reconcile_waits_while_the_broker_position_is_open(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client)

    assert executor.reconcile() == []
    assert len(paper.open_positions) == 1


def test_reconcile_closes_only_the_position_the_broker_closed(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client)
    assert executor.execute(ready_setup(), RISK, epic="GOLD").status == "OPENED"
    client.positions = [broker("pos-2")]
    client.activities = [
        {
            "dateUTC": "2026-09-28T00:20:00.000",
            "dealId": "pos-1",
            "source": "SL",
            "type": "POSITION",
            "details": {"workingOrderId": "sl-order", "level": 97.9, "openPrice": 100.1},
        },
    ]
    client.transactions = [
        {
            "dealId": "pos-1",
            "transactionType": "TRADE",
            "status": "PROCESSED",
            "size": "-0.19",
            "currency": "CHF",
        }
    ]

    events = executor.reconcile()

    assert len(events) == 1
    assert events[0]["exit_reason"] == "STOP"
    assert events[0]["exit_price"] == 97.9
    assert round(events[0]["pnl"], 2) == -0.22
    assert events[0]["broker_pnl"] == -0.19
    assert events[0]["broker_currency"] == "CHF"
    assert events[0]["open_positions_left"] == 1
    assert [position.deal_id for position in paper.open_positions] == ["pos-2"]


def test_reconcile_labels_a_stop_after_trailing_as_trailing_stop(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client)
    client.offer = 103.3
    executor.manage(allow_add=False)
    client.positions = []
    client.activities = [
        {
            "dateUTC": "2026-09-28T00:20:00.000",
            "dealId": "pos-1",
            "source": "SL",
            "type": "POSITION",
            "details": {"level": 101.1, "openPrice": 100.1},
        },
    ]
    client.transactions = [
        {
            "dealId": "pos-1",
            "transactionType": "TRADE",
            "status": "PROCESSED",
            "size": "0.01",
            "currency": "CHF",
        }
    ]

    events = executor.reconcile()

    assert events[0]["exit_reason"] == "TRAILING_STOP"
    assert events[0]["pnl"] > 0
    assert events[0]["broker_pnl"] == 0.01


def test_reconcile_waits_for_broker_pnl_then_records_it(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client, close_lookup_attempts=2)
    client.positions = []
    client.activities = [
        {
            "dateUTC": "2026-09-28T00:20:00.000",
            "dealId": "pos-1",
            "source": "TP",
            "type": "POSITION",
            "details": {"level": 104.1, "openPrice": 100.1},
        },
    ]

    assert executor.reconcile() == []
    assert len(paper.open_positions) == 1

    client.transactions = [
        {
            "dealId": "pos-1",
            "transactionType": "TRADE",
            "status": "PROCESSED",
            "size": "0.37",
            "currency": "chf",
        }
    ]
    events = executor.reconcile()

    assert events[0]["broker_pnl"] == 0.37
    assert events[0]["broker_currency"] == "CHF"


def test_reconcile_falls_back_to_local_pnl_when_transaction_history_lags(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client, close_lookup_attempts=2)
    client.positions = []
    client.activities = [
        {
            "dateUTC": "2026-09-28T00:20:00.000",
            "dealId": "pos-1",
            "source": "TP",
            "type": "POSITION",
            "details": {"level": 104.1, "openPrice": 100.1},
        },
    ]

    assert executor.reconcile() == []
    events = executor.reconcile()

    assert events[0]["exit_reason"] == "TARGET"
    assert events[0]["pnl"] > 0
    assert "broker_pnl" not in events[0]
    assert paper.open_positions == []


def test_reconcile_recovers_after_transaction_history_error(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client, close_lookup_attempts=2)
    client.positions = []
    client.activities = [
        {
            "dateUTC": "2026-09-28T00:20:00.000",
            "dealId": "pos-1",
            "source": "TP",
            "type": "POSITION",
            "details": {"level": 104.1, "openPrice": 100.1},
        },
    ]
    client.transactions = [
        {
            "dealId": "pos-1",
            "transactionType": "TRADE",
            "status": "PROCESSED",
            "size": "0.37",
            "currency": "CHF",
        }
    ]
    client.transaction_errors = 1

    assert executor.reconcile() == []
    events = executor.reconcile()

    assert events[0]["broker_pnl"] == 0.37
    assert len(paper.open_positions) == 0


def test_reconcile_uses_local_pnl_after_persistent_transaction_history_errors(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client, close_lookup_attempts=2)
    client.positions = []
    client.activities = [
        {
            "dateUTC": "2026-09-28T00:20:00.000",
            "dealId": "pos-1",
            "source": "TP",
            "type": "POSITION",
            "details": {"level": 104.1, "openPrice": 100.1},
        },
    ]
    client.transaction_errors = 2

    assert executor.reconcile() == []
    events = executor.reconcile()

    assert events[0]["pnl"] > 0
    assert "broker_pnl" not in events[0]
    assert len(paper.open_positions) == 0


def test_reconcile_resolves_order_id_from_broker_position(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client)
    paper.set_deal_id(paper.open_positions[0].local_id, "order-1")
    client.positions = [
        {"position": {"dealId": "pos-1", "workingOrderId": "order-1"}, "market": {"epic": "GOLD"}}
    ]

    assert executor.reconcile() == []
    assert paper.open_positions[0].deal_id == "pos-1"


def test_reconcile_falls_back_to_nearest_level_when_history_lags(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client, close_lookup_attempts=2)
    client.positions = []
    client.offer = 104.3
    client.activities = [
        {
            "dateUTC": "2026-09-28T00:05:00.000",
            "dealId": "pos-1",
            "source": "USER",
            "type": "POSITION",
            "details": {"workingOrderId": "order-1", "level": 100.1},
        }
    ]

    assert executor.reconcile() == []
    events = executor.reconcile()

    assert events[0]["exit_reason"] == "TARGET_ASSUMED"
    assert paper.open_positions == []


def test_reconcile_never_guesses_while_the_opening_is_missing_from_history(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client, close_lookup_attempts=2)
    client.positions = []

    for _ in range(5):
        assert executor.reconcile() == []
    assert len(paper.open_positions) == 1


def test_manage_labels_a_stop_still_below_entry_as_trail(tmp_path) -> None:
    from goldbot.execution import ManagementConfig

    client = FakeClient()
    executor, paper = opened(
        tmp_path, client, management=ManagementConfig(trail_start_r=0.5, trail_distance_r=1.0)
    )
    client.offer = 101.5  # bid 101.3 = +0.6R; trailed stop 99.3 is still below entry

    events = executor.manage(allow_add=False)

    assert events[0]["reason"] == "TRAIL"
    assert paper.open_positions[0].stop == 99.3


def test_close_all_closes_this_sessions_positions_and_labels_the_exit(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client)
    closed: list[str] = []
    client.close_position = lambda deal_id: closed.append(deal_id) or f"p_{deal_id}"  # type: ignore[attr-defined]

    events = executor.close_all("WEEKEND_CLOSE")

    assert closed == ["pos-1"] and events[0]["reason"] == "WEEKEND_CLOSE"
    client.positions = []
    client.activities = [{
        "dateUTC": "2026-10-02T20:45:00.000", "dealId": "pos-1", "source": "USER", "type": "POSITION",
        "details": {"level": 100.6, "openPrice": 100.1},
    }]
    client.transactions = [{"dealId": "pos-1", "transactionType": "TRADE", "status": "PROCESSED",
                            "size": "0.45", "currency": "CHF"}]
    [event] = executor.reconcile()
    assert event["exit_reason"] == "WEEKEND_CLOSE"
    assert paper.open_positions == []


def _fill(source: str, size: float, level: float, when: str) -> dict[str, object]:
    return {"dateUTC": when, "dealId": "pos-1", "source": source, "type": "POSITION",
            "details": {"size": size, "level": level, "openPrice": 100.1}}


def _trade(reference: str, amount: str) -> dict[str, object]:
    return {"dealId": "pos-1", "transactionType": "TRADE", "status": "PROCESSED",
            "size": amount, "currency": "CHF", "reference": reference}


def test_partial_target_fill_then_stop_is_combined_once(tmp_path) -> None:
    # Issue #70: the broker filled 60% at the target, the rest was stopped later.
    client = FakeClient()
    executor, paper = opened(tmp_path, client, max_size=1)
    assert paper.open_positions[0].units == 1
    client.positions = [{"position": {"dealId": "pos-1", "direction": "BUY", "size": 0.4},
                         "market": {"epic": "GOLD"}}]
    client.activities = [_fill("TP", 0.6, 104.1, "2026-10-02T05:02:05.428")]
    client.transactions = [_trade("t1", "1.90")]

    events = executor.reconcile()

    assert [event["event"] for event in events] == ["PAPER_PARTIAL_CLOSE"]
    assert paper.open_positions[0].partials["units"] == 0.6
    assert executor.reconcile() == []  # the same fill is never counted twice

    # Restart: the partial fill survives in the ledger file.
    reloaded = ledger(tmp_path)
    executor = DemoOrderExecutor(
        client, reloaded, tmp_path / "execution.jsonl", hedging_mode=True, max_size=1  # type: ignore[arg-type]
    )
    client.positions = []
    client.activities.append(_fill("SL", 0.4, 98.1, "2026-10-02T06:46:29.026"))
    client.transactions.append(_trade("t2", "-0.60"))

    events = executor.reconcile()

    assert len(events) == 1 and events[0]["event"] == "PAPER_CLOSE"
    assert events[0]["exit_price"] == pytest.approx(0.6 * 104.1 + 0.4 * 98.1)
    assert events[0]["exit_reason"] == "TARGET+STOP"
    assert events[0]["broker_pnl"] == pytest.approx(1.30)
    assert events[0]["partial_fills"] == 2
    assert events[0]["pnl"] == pytest.approx((0.6 * 104.1 + 0.4 * 98.1 - 100.1) * 1)
    assert reloaded.open_positions == []


def test_partial_and_final_fill_between_polls_are_combined(tmp_path) -> None:
    client = FakeClient()
    executor, _ = opened(tmp_path, client)
    client.positions = []
    client.activities = [_fill("TP", 0.6, 104.1, "2026-10-02T05:02:05"),
                         _fill("SL", 0.4, 98.1, "2026-10-02T06:46:29")]
    client.transactions = [_trade("t1", "1.90"), _trade("t2", "-0.60")]

    events = executor.reconcile()

    assert events[0]["exit_reason"] == "TARGET+STOP"
    assert events[0]["broker_pnl"] == pytest.approx(1.30)
    assert events[0]["exit_price"] == pytest.approx(101.7)



def test_overnight_fee_is_shared_by_units_and_shown_at_close(tmp_path) -> None:
    # Issue #44: one SWAP per market and account, no deal ID. This session holds 1 of 4 units.
    client = FakeClient()
    executor, paper = opened(tmp_path, client, max_size=1)
    client.positions = [
        {"position": {"dealId": "pos-1", "size": 1.0, "createdDateUTC": "2026-09-28T00:05:00"},
         "market": {"epic": "GOLD"}},
        {"position": {"dealId": "other", "size": 3.0, "createdDateUTC": "2026-09-28T01:00:00"},
         "market": {"epic": "GOLD"}},
        {"position": {"dealId": "later", "size": 5.0, "createdDateUTC": "2026-09-29T22:30:00"},
         "market": {"epic": "GOLD"}},
    ]
    client.transactions = [
        {"dateUtc": "2026-09-28T21:00:26", "transactionType": "SWAP", "instrumentName": "GOLD",
         "size": "-4.00", "currency": "CHF", "reference": "fee-1"},
        {"dateUtc": "2026-09-28T21:00:26", "transactionType": "SWAP", "instrumentName": "US100",
         "size": "-9.00", "currency": "CHF", "reference": "fee-2"},
    ]

    events = executor.reconcile()

    assert [e["event"] for e in events] == ["FINANCING"]
    assert events[0]["share"] == 0.25 and events[0]["amount"] == -1.0
    assert paper.open_positions[0].fees == pytest.approx(-1.0)
    executor._last_fee_check = None
    assert executor.reconcile() == []  # the same fee is never allocated twice

    client.positions = []
    client.activities = [_fill("SL", 1.0, 98.1, "2026-09-29T08:00:00")]
    client.transactions.append(_trade("t9", "-1.60"))
    close = executor.reconcile()[-1]
    assert close["event"] == "PAPER_CLOSE" and close["financing"] == -1.0


def test_fee_booked_before_a_position_opened_is_not_charged_to_it(tmp_path) -> None:
    client = FakeClient()
    executor, paper = opened(tmp_path, client, max_size=1)
    client.transactions = [
        {"dateUtc": "2026-09-27T21:00:00", "transactionType": "SWAP", "instrumentName": "GOLD",
         "size": "-4.00", "currency": "CHF", "reference": "old"},
    ]
    events = executor.reconcile()
    assert events[0]["share"] == 0.0 and paper.open_positions[0].fees == 0.0


def test_market_state_is_stored_on_the_opened_position(tmp_path) -> None:
    client = FakeClient()
    executor, paper = executor_for(tmp_path, client, hedging_mode=True)
    result = executor.execute(ready_setup(), RISK, epic="GOLD", context={"label": "sideways"})
    assert result.status == "OPENED"
    assert paper.open_positions[0].context == {"label": "sideways"}
    assert result.paper_event["position"]["context"] == {"label": "sideways"}
