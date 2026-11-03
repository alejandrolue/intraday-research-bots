from __future__ import annotations

from datetime import datetime, timedelta, timezone

from goldbot.capital import CapitalAPIError
from goldbot.models import Candle
from goldbot.paper import PaperLedger
from goldbot.range_breakout import RangeBreakoutStrategy
from goldbot.risk import RiskConfig, RiskEngine
from goldbot.stop_orders import StopOrderBracket

DAY = datetime(2025, 3, 4, tzinfo=timezone.utc)   # Tuesday, London on GMT
NOW = DAY + timedelta(hours=7, minutes=1)


def asia_range() -> list[Candle]:
    out, t = [], DAY
    while t < DAY + timedelta(hours=7):
        out.append(Candle(t, 2900, 2905 if t.minute == 0 else 2902, 2895 if t.minute == 5 else 2898, 2900, spread=0.3))
        t += timedelta(minutes=5)
    return out


class FakeClient:
    def __init__(self):
        self.orders, self.deleted, self.closed, self.positions = [], [], [], []
        self.live_orders = None
        self.activities = []

    def get_market_details(self, epic):
        return {"dealingRules": {}, "snapshot": {"decimalPlacesFactor": 2}}

    def create_working_order(self, **kw):
        self.orders.append(kw)
        return f"ref-{kw['direction']}"

    def delete_working_order(self, deal_id):
        self.deleted.append(deal_id)
        return "ref"

    def get_working_orders(self):
        ids = self.live_orders if self.live_orders is not None else [f"WO-ref-{o['direction']}" for o in self.orders]
        return [{"workingOrderData": {"dealId": i}} for i in ids]

    def get_activity(self):
        if isinstance(self.activities, Exception):
            raise self.activities
        return self.activities

    def close_position(self, deal_id):
        self.closed.append(deal_id)
        return "ref"


class FakeExecutor:
    def __init__(self, client):
        self.client = client

    def _normalized_size(self, units, rules):
        return round(units, 2) if units > 0 else None

    def _confirm(self, reference):
        return {"dealStatus": "ACCEPTED", "dealId": f"WO-{reference}"}

    def _epic_positions(self):
        return self.client.positions


def make(tmp_path, client=None):
    client = client or FakeClient()
    ledger = PaperLedger(tmp_path / "s.json", tmp_path / "j.jsonl", starting_balance=11000, point_value_per_unit=1)
    engine = RiskEngine(RiskConfig(risk_per_trade=0.01, max_trades_per_day=1, max_daily_loss=0.05))
    bracket = StopOrderBracket(client, FakeExecutor(client), ledger, engine, tmp_path / "bracket.json", epic="GOLD")
    return bracket, client, ledger


STRATEGY = RangeBreakoutStrategy("rangebreak-london")


def test_places_a_buy_and_a_sell_stop_at_the_range_edges_once(tmp_path):
    bracket, client, _ = make(tmp_path)
    events = bracket.sync(STRATEGY, asia_range, NOW)
    assert [e["event"] for e in events] == ["BRACKET_PLACED", "BRACKET_PLACED"]
    buy, sell = client.orders
    assert (buy["direction"], buy["level"], buy["stop_level"]) == ("BUY", 2905.3, 2895)
    assert (sell["direction"], sell["level"], sell["stop_level"]) == ("SELL", 2894.7, 2905)
    assert buy["good_till"] == DAY + timedelta(hours=12)
    assert buy["size"] == round(110 / 10.3, 2)                    # 1% of 11,000 over 10.3 points
    assert bracket.sync(STRATEGY, asia_range, NOW + timedelta(minutes=1)) == []
    # a restart reads the state file and does not place again
    again, client2, _ = make(tmp_path)
    assert again.sync(STRATEGY, asia_range, NOW + timedelta(minutes=2)) == [] and client2.orders == []


def test_no_orders_outside_the_window_or_when_blocked(tmp_path):
    bracket, client, _ = make(tmp_path)
    assert bracket.sync(STRATEGY, asia_range, DAY + timedelta(hours=6, minutes=59)) == []
    assert bracket.sync(STRATEGY, asia_range, NOW, blocked="paused") == []
    assert client.orders == []


def test_a_fill_opens_the_ledger_position_and_cancels_the_other_side(tmp_path):
    bracket, client, ledger = make(tmp_path)
    bracket.sync(STRATEGY, asia_range, NOW)
    client.positions = [{"position": {"dealId": "P1", "workingOrderId": "WO-ref-BUY", "level": 2905.5,
                                      "size": 10.68, "stopLevel": 2895, "profitLevel": 3008.3}}]
    events = bracket.sync(STRATEGY, asia_range, NOW + timedelta(minutes=10))
    filled = [e for e in events if e["event"] == "BRACKET_FILLED"][0]
    assert filled["fill"] == 2905.5 and abs(filled["slippage_points"] - 0.2) < 1e-9
    assert client.deleted == ["WO-ref-SELL"]
    [position] = ledger.open_positions
    assert position.direction == "BUY" and position.entry == 2905.5 and position.deal_id == "P1"
    assert ledger.state.trades_today == 1


def test_both_sides_filling_closes_the_second(tmp_path):
    bracket, client, ledger = make(tmp_path)
    bracket.sync(STRATEGY, asia_range, NOW)
    client.positions = [
        {"position": {"dealId": "P1", "workingOrderId": "WO-ref-BUY", "level": 2905.3, "size": 10.68}},
        {"position": {"dealId": "P2", "workingOrderId": "WO-ref-SELL", "level": 2894.7, "size": 10.68}},
    ]
    events = bracket.sync(STRATEGY, asia_range, NOW + timedelta(minutes=10))
    assert client.closed == ["P2"] and "BRACKET_BOTH_FILLED" in [e["event"] for e in events]
    assert len(ledger.open_positions) == 1


def test_pending_orders_are_cancelled_at_expiry_and_when_blocked(tmp_path):
    bracket, client, _ = make(tmp_path)
    bracket.sync(STRATEGY, asia_range, NOW)
    events = bracket.sync(STRATEGY, asia_range, DAY + timedelta(hours=12, minutes=1))
    assert sorted(client.deleted) == ["WO-ref-BUY", "WO-ref-SELL"]
    assert {e["reason"] for e in events if e["event"] == "BRACKET_CANCELLED"} == {"EXPIRED"}
    bracket2, client2, _ = make(tmp_path / "b")
    bracket2.sync(STRATEGY, asia_range, NOW)
    bracket2.sync(STRATEGY, asia_range, NOW + timedelta(minutes=5), blocked="Weekend close in 50 minutes")
    assert sorted(client2.deleted) == ["WO-ref-BUY", "WO-ref-SELL"]


def test_orders_gone_at_the_broker_without_a_position_are_marked(tmp_path):
    bracket, client, ledger = make(tmp_path)
    bracket.sync(STRATEGY, asia_range, NOW)
    client.live_orders = ["WO-ref-SELL"]
    bracket.sync(STRATEGY, asia_range, NOW + timedelta(minutes=5))
    assert bracket.state["orders"]["BUY"]["status"] == "GONE"
    assert bracket.state["orders"]["SELL"]["status"] == "PENDING" and not ledger.open_positions


def test_a_failed_cancel_is_recorded_not_raised(tmp_path):
    client = FakeClient()

    def refuse(deal_id):
        raise CapitalAPIError("already filled")

    client.delete_working_order = refuse
    bracket, _, _ = make(tmp_path, client)
    bracket.sync(STRATEGY, asia_range, NOW)
    events = bracket.sync(STRATEGY, asia_range, NOW, blocked="paused")
    assert {e["event"] for e in events} == {"BRACKET_CANCEL_FAILED"}


def test_no_orders_once_the_range_already_broke(tmp_path):
    bracket, client, _ = make(tmp_path)
    broke = asia_range() + [Candle(DAY + timedelta(hours=7), 2900, 2906, 2899, 2905.5, spread=0.3)]
    assert bracket.sync(STRATEGY, lambda: broke, NOW + timedelta(minutes=5)) == []
    assert client.orders == []


def test_a_fill_without_a_working_order_link_is_matched_by_side_and_size(tmp_path):
    bracket, client, ledger = make(tmp_path)
    bracket.sync(STRATEGY, asia_range, NOW)
    size = client.orders[1]["size"]
    client.live_orders = ["WO-ref-BUY"]
    client.positions = [{"position": {"dealId": "P9", "direction": "SELL", "size": size, "level": 2894.6}}]
    events = bracket.sync(STRATEGY, asia_range, NOW + timedelta(minutes=5))
    assert ledger.open_positions[0].deal_id == "P9" and client.deleted == ["WO-ref-BUY"]
    assert "BRACKET_FILLED" in [e["event"] for e in events]


def test_a_wide_spread_waits_and_retries_instead_of_skipping_the_day(tmp_path):
    client = FakeClient()
    ledger = PaperLedger(tmp_path / "s.json", tmp_path / "j.jsonl", starting_balance=11000, point_value_per_unit=1)
    engine = RiskEngine(RiskConfig(risk_per_trade=0.01, max_trades_per_day=1, max_daily_loss=0.05, max_spread=1.0))
    bracket = StopOrderBracket(client, FakeExecutor(client), ledger, engine, tmp_path / "bracket.json", epic="GOLD")

    def wide():
        out = asia_range()
        out[-1] = Candle(out[-1].timestamp, 2900, 2902, 2898, 2900, spread=1.95)
        return out

    events = bracket.sync(STRATEGY, wide, NOW)
    assert [e["event"] for e in events] == ["BRACKET_SPREAD_WAIT"] and client.orders == []
    assert bracket.sync(STRATEGY, wide, NOW + timedelta(minutes=1)) == []          # journaled once per day
    events = bracket.sync(STRATEGY, asia_range, NOW + timedelta(minutes=2))       # spread back to 0.3
    assert [e["event"] for e in events] == ["BRACKET_PLACED", "BRACKET_PLACED"]
    assert client.orders[0]["level"] == 2905.3


def test_a_portfolio_budget_caps_the_bracket_size(tmp_path):
    bracket, client, _ = make(tmp_path)
    bracket.sync(STRATEGY, asia_range, NOW, max_risk_budget=51.5)          # 51.5 CHF over 10.3 points = 5 units
    assert [o["size"] for o in client.orders] == [5.0, 5.0]


def test_a_fill_between_the_position_and_order_reads_is_still_registered(tmp_path):
    # 28 Oct 2026 (#174): the order filled after the positions were read but before the order list was.
    bracket, client, ledger = make(tmp_path)
    bracket.sync(STRATEGY, asia_range, NOW)
    client.live_orders = ["WO-ref-BUY"]
    fill = [{"position": {"dealId": "P1", "workingOrderId": "WO-ref-SELL", "level": 2894.6, "size": 10.68}}]
    reads = iter([[], fill])
    bracket.executor._epic_positions = lambda: next(reads)
    events = bracket.sync(STRATEGY, asia_range, NOW + timedelta(minutes=5))
    assert "BRACKET_FILLED" in [e["event"] for e in events]
    assert client.deleted == ["WO-ref-BUY"]
    [position] = ledger.open_positions
    assert position.direction == "SELL" and position.deal_id == "P1"


def test_a_fill_that_already_closed_is_registered_from_the_activity_log(tmp_path):
    bracket, client, ledger = make(tmp_path)
    bracket.sync(STRATEGY, asia_range, NOW)
    client.live_orders = ["WO-ref-BUY"]
    client.activities = [
        {"type": "WORKING_ORDER", "status": "EXECUTED", "dealId": "WO-ref-SELL"},
        {"type": "POSITION", "status": "ACCEPTED", "dealId": "P9",
         "details": {"workingOrderId": "WO-ref-SELL", "direction": "SELL", "size": 10.68, "level": 2894.5}},
    ]
    bracket.sync(STRATEGY, asia_range, NOW + timedelta(minutes=5))
    [position] = ledger.open_positions        # reconciliation then closes it from the broker's real exit
    assert position.deal_id == "P9" and position.entry == 2894.5 and position.stop == 2905
    assert bracket.state["filled"] == "SELL" and client.deleted == ["WO-ref-BUY"]


def test_an_executed_order_without_a_position_yet_stays_pending(tmp_path):
    bracket, client, ledger = make(tmp_path)
    bracket.sync(STRATEGY, asia_range, NOW)
    client.live_orders = ["WO-ref-BUY"]
    client.activities = [{"type": "WORKING_ORDER", "status": "EXECUTED", "dealId": "WO-ref-SELL"}]
    bracket.sync(STRATEGY, asia_range, NOW + timedelta(minutes=5))
    assert bracket.state["orders"]["SELL"]["status"] == "PENDING" and not ledger.open_positions
    client.activities = CapitalAPIError("activity down")
    bracket.sync(STRATEGY, asia_range, NOW + timedelta(minutes=6))
    assert bracket.state["orders"]["SELL"]["status"] == "PENDING"
    client.activities = [{"type": "WORKING_ORDER", "status": "EXPIRED", "dealId": "WO-ref-SELL"}]
    bracket.sync(STRATEGY, asia_range, NOW + timedelta(minutes=7))
    assert bracket.state["orders"]["SELL"]["status"] == "GONE"
