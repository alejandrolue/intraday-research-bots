from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from goldbot.models import AIReview, MarketSetup
from goldbot.paper import PaperLedger
from goldbot.risk import RiskConfig, RiskDecision, RiskEngine
from goldbot.runner import BotResult
from goldbot.telegram import (
    ApprovalQueue,
    TelegramApprovalService,
    TelegramBotClient,
    TelegramStatusNotifier,
)


NOW = datetime.now(timezone.utc)


def result() -> BotResult:
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
    return BotResult(
        setup=setup,
        risk=risk,
        ai_review=AIReview("APPROVE", 0.8, (), "test"),
        paper_events=(),
        paper_entry_approved=True,
        observed_spread=0.5,
    )


def service(tmp_path, updates: list[dict[str, object]]) -> tuple[TelegramApprovalService, PaperLedger]:
    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "sendMessage":
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
        if method == "getUpdates":
            return httpx.Response(200, json={"ok": True, "result": updates})
        if method in {"answerCallbackQuery", "editMessageReplyMarkup"}:
            return httpx.Response(200, json={"ok": True, "result": True})
        raise AssertionError(f"Unexpected Telegram method: {method}")

    client = TelegramBotClient("test-token", transport=httpx.MockTransport(handler))
    ledger = PaperLedger(
        tmp_path / "paper.json",
        tmp_path / "journal.jsonl",
        starting_balance=10_000,
        point_value_per_unit=1,
    )
    approval_service = TelegramApprovalService(
        client,
        chat_id="123",
        allowed_user_id=456,
        queue=ApprovalQueue(tmp_path / "telegram.json"),
        ledger=ledger,
        risk_engine=RiskEngine(RiskConfig()),
        approval_ttl_seconds=600,
    )
    return approval_service, ledger


def test_authorized_callback_opens_only_a_paper_position(tmp_path) -> None:
    updates: list[dict[str, object]] = []
    approval_service, ledger = service(tmp_path, updates)
    staged = approval_service.stage(result(), spread=0.5)
    assert staged is not None
    proposal_id = staged["proposal_id"]
    updates.append(
        {
            "update_id": 100,
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 456},
                "message": {"message_id": 42, "chat": {"id": 123}},
                "data": f"gb:a:{proposal_id}",
            },
        }
    )

    events = approval_service.poll()

    assert events[0]["event"] == "TELEGRAM_APPROVED"
    assert len(ledger.open_positions) == 1
    assert approval_service.queue.pending == {}
    saved = json.loads((tmp_path / "telegram.json").read_text(encoding="utf-8"))
    assert saved["offset"] == 101


def test_unauthorized_callback_cannot_open_paper_position(tmp_path) -> None:
    updates: list[dict[str, object]] = []
    approval_service, ledger = service(tmp_path, updates)
    staged = approval_service.stage(result(), spread=0.5)
    assert staged is not None
    proposal_id = staged["proposal_id"]
    updates.append(
        {
            "update_id": 200,
            "callback_query": {
                "id": "callback-2",
                "from": {"id": 999},
                "message": {"message_id": 42, "chat": {"id": 123}},
                "data": f"gb:a:{proposal_id}",
            },
        }
    )

    events = approval_service.poll()

    assert events[0]["event"] == "TELEGRAM_UNAUTHORIZED"
    assert ledger.open_positions == []
    assert proposal_id in approval_service.queue.pending


def test_status_notifier_reports_both_strategy_decisions() -> None:
    messages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        messages.append(payload["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = TelegramBotClient("test-token", transport=httpx.MockTransport(handler))
    notifier = TelegramStatusNotifier(client, chat_id="123")
    notifier.trend_resolution = "HOUR"
    primary = result()
    secondary = MarketSetup(
        decision="WATCH_SHORT",
        timestamp=NOW,
        reason="test",
        bias="BEARISH",
        strategy="ema_pullback",
    )
    combined = BotResult(
        setup=primary.setup,
        risk=primary.risk,
        ai_review=primary.ai_review,
        paper_events=(),
        paper_entry_approved=True,
        observed_spread=0.5,
        strategy_setups=(primary.setup, secondary),
        higher_timeframe_bias="BULLISH",
    )

    notifier.decision(combined)
    client.close()

    assert "unspecified: READY_LONG" in messages[0]
    assert "ema_pullback: WATCH_SHORT" in messages[0]
    assert "HOUR trend: BULLISH" in messages[0]


def test_status_notifier_reports_trade_open_and_close() -> None:
    messages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        messages.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = TelegramBotClient("test-token", transport=httpx.MockTransport(handler))
    notifier = TelegramStatusNotifier(client, chat_id="123")
    position = {
        "direction": "BUY",
        "entry": 4131.59,
        "stop": 4126.72,
        "target": 4138.62,
        "units": 0.1,
        "opened_at": "2026-09-29T05:15:00+00:00",
        "strategy": "ema_pullback",
        "deal_id": "pos-1",
    }

    notifier.trade_opened({"position": position, "risk_budget": 0.487}, deal_id="pos-1")
    notifier.trade_closed(
        {
            "position": position,
            "exit_reason": "STOP",
            "exit_price": 4126.52,
            "pnl": -0.507,
            "broker_pnl": -0.46,
            "broker_currency": "CHF",
            "balance": 9997.35,
            "daily_realized_pnl": -2.46,
            "timestamp": "2026-09-29T05:30:30+00:00",
        }
    )
    client.close()

    assert "Trade opened: BUY Gold (Capital.com demo)" in messages[0]
    assert "R:R: 1.44" in messages[0]
    assert "Deal ID: pos-1" in messages[0]
    assert "Trade closed: STOP" in messages[1]
    assert "Entry 4131.59 → Exit 4126.52 (-5.07 pts)" in messages[1]
    assert "Broker P&L: CHF -0.46" in messages[1]
    assert "Local estimate: USD -0.51 (-1.04R)" in messages[1]
    assert "Held: 15m" in messages[1]
    assert "Local balance estimate: USD 9,997.35" in messages[1]


def test_status_notifier_labels_a_close_without_broker_pnl_as_local() -> None:
    messages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        messages.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = TelegramBotClient("test-token", transport=httpx.MockTransport(handler))
    notifier = TelegramStatusNotifier(client, chat_id="123")

    notifier.trade_closed(
        {
            "position": {
                "direction": "SELL",
                "entry": 100.0,
                "stop": 102.0,
                "target": 97.0,
                "units": 0.1,
                "strategy": "ema_pullback",
            },
            "exit_reason": "TARGET_ASSUMED",
            "exit_price": 97.0,
            "pnl": 0.3,
            "balance": 1000.3,
        }
    )
    client.close()

    assert "P&L (local estimate): USD +0.30 (+1.50R)" in messages[0]
    assert "Broker P&L" not in messages[0]


def test_status_notifier_reports_stop_and_target_changes() -> None:
    messages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        messages.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = TelegramBotClient("test-token", transport=httpx.MockTransport(handler))
    notifier = TelegramStatusNotifier(client, chat_id="123", label="5m")
    position = {
        "direction": "BUY", "entry": 100.1, "stop": 100.1, "target": 106.1,
        "strategy": "ema_pullback", "initial_stop": 98.1,
    }

    notifier.trade_managed(
        {
            "position": position, "reason": "BREAKEVEN+TARGET_EXTENDED", "price": 103.7,
            "profit_r": 1.8, "locked_r": 0.0, "old_stop": 98.1, "stop": 100.1,
            "old_target": 104.1, "target": 106.1,
        }
    )
    client.close()

    assert messages[0].startswith("[5m] 🔒 Stop moved: this trade can no longer lose")
    assert "🎯 Target extended" in messages[0]
    assert "Stop: 98.10 → 100.10 (locks +0.00R)" in messages[0]
    assert "Target: 104.10 → 106.10" in messages[0]


def test_trade_messages_name_the_market() -> None:
    messages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        messages.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = TelegramBotClient("test-token", transport=httpx.MockTransport(handler))
    notifier = TelegramStatusNotifier(client, chat_id="123", label="us100-orb", market="US100")
    position = {"direction": "SELL", "entry": 30280.0, "stop": 30320.0, "target": 30200.0,
                "units": 0.006, "strategy": "orb-nycash-15-rr2"}

    notifier.trade_opened({"position": position, "risk_budget": 0.24}, deal_id="pos-9")
    client.close()

    assert messages[0].startswith("[us100-orb] 🟢 Trade opened: SELL US100")
