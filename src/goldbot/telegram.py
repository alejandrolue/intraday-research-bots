from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from .models import AIReview, MarketSetup
from .paper import PaperLedger
from .risk import RiskDecision, RiskEngine
from .runner import BotResult


class TelegramAPIError(RuntimeError):
    pass


class TelegramBotClient:
    def __init__(
        self,
        token: str,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=f"https://api.telegram.org/bot{token}/",
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._call("sendMessage", json=payload)

    def get_updates(
        self,
        *,
        offset: int | None = None,
        limit: int = 100,
        timeout: int = 0,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        result = self._call("getUpdates", params=params, method="GET")
        if not isinstance(result, list):
            raise TelegramAPIError("Telegram getUpdates returned an unexpected payload")
        return result

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: str,
        *,
        show_alert: bool = False,
    ) -> None:
        self._call(
            "answerCallbackQuery",
            json={
                "callback_query_id": callback_query_id,
                "text": text[:200],
                "show_alert": show_alert,
            },
        )

    def set_my_commands(self, commands: list[tuple[str, str]]) -> None:
        self._call(
            "setMyCommands",
            json={
                "commands": [
                    {"command": name, "description": description} for name, description in commands
                ]
            },
        )

    def remove_inline_keyboard(self, chat_id: str, message_id: int) -> None:
        self._call(
            "editMessageReplyMarkup",
            json={"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
        )

    def _call(
        self,
        api_method: str,
        *,
        method: str = "POST",
        **kwargs: Any,
    ) -> Any:
        try:
            response = self._client.request(method, api_method, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramAPIError(f"Telegram API request failed for {api_method}") from exc
        if not payload.get("ok"):
            raise TelegramAPIError(f"Telegram API rejected {api_method}")
        return payload.get("result")


_LABELS = {
    "sideways": "sideways",
    "trend_up": "trending up",
    "trend_down": "trending down",
    "breakout_up": "breakout up",
    "breakout_down": "breakout down",
    "mixed": "mixed",
}


def _market_line(position: dict[str, Any], *, prefix: str = "Market") -> str | None:
    """One line describing the market state stored with the position (issue #77)."""
    context = position.get("context") or {}
    if not context.get("label"):
        return None
    return (
        f"{prefix}: {_LABELS.get(context['label'], context['label'])} "
        f"(ER {context.get('efficiency', 0):.2f}, ADX {context.get('adx', 0):.0f}, "
        f"at {context.get('position_in_range', 0):.0%} of the 20-candle range)"
    )


def _held_for(opened_at: Any, closed_at: Any) -> str | None:
    try:
        seconds = (
            datetime.fromisoformat(str(closed_at)) - datetime.fromisoformat(str(opened_at))
        ).total_seconds()
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    hours, remainder = divmod(int(seconds) // 60, 60)
    return f"{hours}h {remainder}m" if hours else f"{remainder}m"


class TelegramStatusNotifier:
    def __init__(
        self,
        client: TelegramBotClient,
        *,
        chat_id: str,
        label: str | None = None,
        market: str = "Gold",
    ) -> None:
        self.client = client
        self.chat_id = str(chat_id)
        self.label = label
        self.market = market
        self.entry_resolution = "entry"
        self.trend_resolution = "Higher-timeframe"
        self._last_error: str | None = None

    def _send(self, text: str) -> None:
        self.client.send_message(self.chat_id, f"[{self.label}] {text}" if self.label else text)

    def startup(
        self,
        *,
        entry_resolution: str,
        trend_resolution: str,
        interval: int,
        max_trades: int,
        strategies: tuple[str, ...] = ("breakout_retest", "ema_pullback", "demand_zone_rejection"),
        max_positions: int | None = None,
        manage: bool = True,
    ) -> None:
        self.entry_resolution = entry_resolution
        self.trend_resolution = trend_resolution
        self._send(
            "Goldbot demo executor started\n"
            f"Entry candles: {entry_resolution}\n"
            f"Trend filter: {trend_resolution}\n"
            f"Polling: {interval}s\n"
            f"Strategies: {', '.join(strategies)}\n"
            f"Daily trade cap: {max_trades}\n"
            + (f"Max open positions: {max_positions}\n" if max_positions is not None else "")
            + ("Trade management: trailing, target extension, adds\n" if manage
               else "Trade management: off (fixed stop and target)\n")
            + 
            "Broker: Capital.com DEMO only\n"
            f"Market: {self.market}",
        )

    def decision(self, result: BotResult) -> None:
        self._last_error = None
        setups = result.strategy_setups or (result.setup,)
        strategy_lines = [
            f"{setup.strategy}: {setup.decision} ({setup.bias})" for setup in setups
        ]
        event_lines = [
            f"Local event: {event.get('event')}"
            for event in result.paper_events
        ]
        self._send(
            f"Goldbot {self.entry_resolution}/{self.trend_resolution} update\n"
            f"Candle: {result.setup.timestamp.isoformat()}\n"
            f"{self.trend_resolution} trend: {result.higher_timeframe_bias}\n"
            + (
                f"Confirming trend: {result.confirm_bias}\n"
                if result.confirm_bias != "NOT_USED"
                else ""
            )
            + "\n".join(strategy_lines)
            + "\n"
            f"Selected: {result.setup.strategy} / {result.setup.decision}\n"
            f"Risk: {'PASS' if result.risk.approved else 'BLOCK'} - {result.risk.reason}\n"
            f"Spread: {result.observed_spread:.2f}"
            + ("\n" + "\n".join(event_lines) if event_lines else ""),
        )

    def execution(self, event: dict[str, Any]) -> None:
        lines = [
            "Goldbot demo execution",
            f"Status: {event.get('status')}",
            f"Strategy: {event.get('strategy')}",
            f"Reason: {event.get('reason')}",
        ]
        if event.get("size") is not None:
            lines.append(f"Size: {event['size']}")
        if event.get("deal_id"):
            lines.append(f"Deal ID: {event['deal_id']}")
        self._send("\n".join(lines))

    def trade_opened(self, event: dict[str, Any], *, deal_id: str | None = None) -> None:
        position = event["position"]
        entry, stop, target = position["entry"], position["stop"], position["target"]
        risk_points = abs(entry - stop)
        reward_points = abs(target - entry)
        heading = (
            f"➕ Added to winner: {position['direction']} {self.market}"
            if position.get("role") == "add"
            else f"🟢 Trade opened: {position['direction']} {self.market}"
        )
        lines = [
            heading + (" (Capital.com demo)" if deal_id else " (paper)"),
            f"Strategy: {position.get('strategy')}",
            f"Entry: {entry:.2f}",
            f"Stop: {stop:.2f} ({risk_points:.2f} pts)",
            f"Target: {target:.2f} ({reward_points:.2f} pts)",
            f"R:R: {reward_points / risk_points:.2f}" if risk_points else "R:R: n/a",
            f"Size: {position['units']:g}",
            f"Risk: ${float(event.get('risk_budget') or 0):.2f}",
        ]
        market = _market_line(position)
        if market:
            lines.append(market)
        if event.get("open_positions", 1) > 1:
            lines.append(f"Open positions: {event['open_positions']}")
        if deal_id:
            lines.append(f"Deal ID: {deal_id}")
        self._send("\n".join(lines))

    def trade_managed(self, event: dict[str, Any]) -> None:
        position = event["position"]
        reasons = str(event.get("reason") or "").split("+")
        locked_r = float(event.get("locked_r") or 0.0)
        lines = []
        if "BREAKEVEN" in reasons:
            lines.append("🔒 Stop moved: this trade can no longer lose")
        elif "TRAIL" in reasons:
            lines.append(f"📈 Trailing stop raised: locks {locked_r:+.2f}R")
        if "TARGET_EXTENDED" in reasons:
            lines.append("🎯 Target extended to let the winner run")
        lines += [
            f"{position['direction']} {self.market} · {position.get('strategy')} · entry {position['entry']:.2f}",
            f"Price: {float(event['price']):.2f} ({float(event['profit_r']):+.2f}R)",
        ]
        if event["stop"] != event["old_stop"]:
            lines.append(f"Stop: {event['old_stop']:.2f} → {event['stop']:.2f} (locks {locked_r:+.2f}R)")
        if event["target"] != event["old_target"]:
            lines.append(f"Target: {event['old_target']:.2f} → {event['target']:.2f}")
        self._send("\n".join(lines))

    def trade_closed(self, event: dict[str, Any]) -> None:
        position = event["position"]
        entry, exit_price = position["entry"], float(event["exit_price"])
        pnl = float(event["pnl"])
        broker_pnl = (
            float(event["broker_pnl"]) if event.get("broker_pnl") is not None else None
        )
        displayed_pnl = broker_pnl if broker_pnl is not None else pnl
        points = (exit_price - entry) * (1 if position["direction"] == "BUY" else -1)
        risk_points = abs(entry - (position.get("initial_stop") or position["stop"]))
        icon = "✅" if displayed_pnl > 0 else "❌" if displayed_pnl < 0 else "➖"
        lines = [
            f"{icon} Trade closed: {event.get('exit_reason')}",
            f"{position['direction']} {self.market} · {position.get('strategy')}"
            + (" · added position" if position.get("role") == "add" else ""),
            f"Entry {entry:.2f} → Exit {exit_price:.2f} ({points:+.2f} pts)",
        ]
        if broker_pnl is not None:
            currency = str(event.get("broker_currency") or "account currency").upper()
            lines.append(f"Broker P&L: {currency} {broker_pnl:+.2f}")
            lines.append(
                f"Local estimate: USD {pnl:+.2f}"
                + (f" ({points / risk_points:+.2f}R)" if risk_points else "")
            )
        else:
            lines.append(
                f"P&L (local estimate): USD {pnl:+.2f}"
                + (f" ({points / risk_points:+.2f}R)" if risk_points else "")
            )
        if event.get("financing"):
            fees = float(event["financing"])
            currency = str(event.get("broker_currency") or "CHF").upper()
            lines.append(
                f"Overnight fees: {currency} {fees:+.2f}"
                + (f" · net {currency} {broker_pnl + fees:+.2f}" if broker_pnl is not None else "")
            )
        if event.get("partial_fills"):
            lines.append(f"Closed in {event['partial_fills']} fills (average exit shown)")
        market = _market_line(position, prefix="Market at entry")
        if market:
            lines.append(market)
        held = _held_for(position.get("opened_at"), event.get("timestamp"))
        if held:
            lines.append(f"Held: {held}")
        path = event.get("path")
        if path:
            lines.append(
                f"Path: best {float(path['mfe_r']):+.2f}R, worst {float(path['mae_r']):+.2f}R"
                + (" (recorded from mid-trade)" if path.get("partial") else "")
            )
        if event.get("open_positions_left"):
            lines.append(f"Still open: {event['open_positions_left']}")
        if event.get("daily_realized_pnl") is not None:
            lines.append(f"Local today estimate: USD {float(event['daily_realized_pnl']):+.2f}")
        lines.append(f"Local balance estimate: USD {float(event['balance']):,.2f}")
        self._send("\n".join(lines))

    def error(self, message: str) -> None:
        if message == self._last_error:
            return
        self._last_error = message
        self._send(f"Goldbot error\n{message}")


@dataclass
class PendingProposal:
    proposal_id: str
    created_at: str
    expires_at: str
    message_id: int
    spread: float
    setup: dict[str, Any]
    risk: dict[str, Any]
    ai_review: dict[str, Any]

    def expired(self, now: datetime) -> bool:
        return datetime.fromisoformat(self.expires_at) <= now

    def market_setup(self) -> MarketSetup:
        payload = dict(self.setup)
        payload.pop("direction", None)
        payload["timestamp"] = datetime.fromisoformat(payload["timestamp"])
        return MarketSetup(**payload)


class ApprovalQueue:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset: int | None = None
        self.pending: dict[str, PendingProposal] = {}
        self._load()

    def add(self, proposal: PendingProposal) -> None:
        self.pending[proposal.proposal_id] = proposal
        self._save()

    def pop(self, proposal_id: str) -> PendingProposal | None:
        proposal = self.pending.pop(proposal_id, None)
        self._save()
        return proposal

    def set_offset(self, offset: int) -> None:
        self.offset = offset
        self._save()

    def expire(self, now: datetime) -> list[PendingProposal]:
        expired = [proposal for proposal in self.pending.values() if proposal.expired(now)]
        for proposal in expired:
            self.pending.pop(proposal.proposal_id, None)
        if expired:
            self._save()
        return expired

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.offset = payload.get("offset")
        self.pending = {
            item["proposal_id"]: PendingProposal(**item)
            for item in payload.get("pending", [])
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "offset": self.offset,
            "pending": [asdict(item) for item in self.pending.values()],
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)


class TelegramApprovalService:
    def __init__(
        self,
        client: TelegramBotClient,
        *,
        chat_id: str,
        allowed_user_id: int,
        queue: ApprovalQueue,
        ledger: PaperLedger,
        risk_engine: RiskEngine,
        approval_ttl_seconds: int = 600,
    ) -> None:
        self.client = client
        self.chat_id = str(chat_id)
        self.allowed_user_id = allowed_user_id
        self.queue = queue
        self.ledger = ledger
        self.risk_engine = risk_engine
        self.approval_ttl_seconds = approval_ttl_seconds

    def stage(self, result: BotResult, *, spread: float) -> dict[str, Any] | None:
        if not result.paper_entry_approved:
            return None
        if self.queue.pending:
            return {"event": "TELEGRAM_SKIPPED", "reason": "An approval is already pending."}

        proposal_id = uuid4().hex[:12]
        created_at = datetime.now(timezone.utc)
        expires_at = created_at + timedelta(seconds=self.approval_ttl_seconds)
        message = self._proposal_message(result, expires_at)
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "Approve paper trade", "callback_data": f"gb:a:{proposal_id}"},
                    {"text": "Reject", "callback_data": f"gb:r:{proposal_id}"},
                ]
            ]
        }
        sent = self.client.send_message(self.chat_id, message, reply_markup=reply_markup)
        proposal = PendingProposal(
            proposal_id=proposal_id,
            created_at=created_at.isoformat(),
            expires_at=expires_at.isoformat(),
            message_id=int(sent["message_id"]),
            spread=spread,
            setup=result.setup.to_dict(),
            risk=result.risk.to_dict(),
            ai_review=result.ai_review.to_dict(),
        )
        self.queue.add(proposal)
        return {"event": "TELEGRAM_PROPOSAL", "proposal_id": proposal_id}

    def poll(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for proposal in self.queue.expire(now):
            self._remove_keyboard(proposal)
            self.client.send_message(
                self.chat_id,
                f"Paper proposal {proposal.proposal_id} expired without approval.",
            )
            events.append({"event": "TELEGRAM_EXPIRED", "proposal_id": proposal.proposal_id})

        updates = self.client.get_updates(offset=self.queue.offset, timeout=0)
        for update in updates:
            update_id = int(update["update_id"])
            self.queue.set_offset(update_id + 1)
            callback = update.get("callback_query")
            if not isinstance(callback, dict):
                continue
            event = self._handle_callback(callback, now)
            if event:
                events.append(event)
        return events

    def _handle_callback(self, callback: dict[str, Any], now: datetime) -> dict[str, Any] | None:
        callback_id = str(callback.get("id", ""))
        sender_id = int((callback.get("from") or {}).get("id", 0))
        message = callback.get("message") or {}
        callback_chat_id = str((message.get("chat") or {}).get("id", ""))
        data = str(callback.get("data", ""))

        if sender_id != self.allowed_user_id or callback_chat_id != self.chat_id:
            self.client.answer_callback_query(
                callback_id,
                "This user or chat is not authorized.",
                show_alert=True,
            )
            return {"event": "TELEGRAM_UNAUTHORIZED", "user_id": sender_id}

        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "gb" or parts[1] not in {"a", "r"}:
            self.client.answer_callback_query(callback_id, "Unknown action.", show_alert=True)
            return None
        action, proposal_id = parts[1], parts[2]
        proposal = self.queue.pending.get(proposal_id)
        if proposal is None:
            self.client.answer_callback_query(callback_id, "Proposal is missing or already handled.")
            return None
        if proposal.expired(now):
            self.queue.pop(proposal_id)
            self._remove_keyboard(proposal)
            self.client.answer_callback_query(callback_id, "Proposal expired.", show_alert=True)
            return {"event": "TELEGRAM_EXPIRED", "proposal_id": proposal_id}

        if action == "r":
            self.queue.pop(proposal_id)
            self._remove_keyboard(proposal)
            self.client.answer_callback_query(callback_id, "Paper proposal rejected.")
            return {"event": "TELEGRAM_REJECTED", "proposal_id": proposal_id}

        setup = proposal.market_setup()
        risk = self.risk_engine.evaluate(
            setup,
            spread=proposal.spread,
            state=self.ledger.risk_state(now),
        )
        if not risk.approved:
            self.queue.pop(proposal_id)
            self._remove_keyboard(proposal)
            self.client.answer_callback_query(callback_id, risk.reason, show_alert=True)
            return {
                "event": "TELEGRAM_APPROVAL_BLOCKED",
                "proposal_id": proposal_id,
                "reason": risk.reason,
            }

        paper_event = self.ledger.open_position(setup, risk)
        self.queue.pop(proposal_id)
        self._remove_keyboard(proposal)
        self.client.answer_callback_query(callback_id, "Paper trade approved and recorded.")
        self.client.send_message(
            self.chat_id,
            f"Paper trade opened: {setup.direction} at {setup.entry:.2f}, "
            f"stop {setup.stop:.2f}, target {setup.target:.2f}.",
        )
        return {
            "event": "TELEGRAM_APPROVED",
            "proposal_id": proposal_id,
            "paper_event": paper_event,
        }

    def _remove_keyboard(self, proposal: PendingProposal) -> None:
        try:
            self.client.remove_inline_keyboard(self.chat_id, proposal.message_id)
        except TelegramAPIError:
            pass

    @staticmethod
    def _proposal_message(result: BotResult, expires_at: datetime) -> str:
        setup = result.setup
        ai = result.ai_review
        return (
            "Goldbot PAPER proposal\n\n"
            f"Direction: {setup.direction}\n"
            f"Signal time: {setup.timestamp.isoformat()}\n"
            f"Entry: {setup.entry:.2f}\n"
            f"Stop: {setup.stop:.2f}\n"
            f"Target: {setup.target:.2f}\n"
            f"Simulated units: {result.risk.units:.4f}\n"
            f"Risk budget: {result.risk.risk_budget:.2f}\n"
            f"AI review: {ai.verdict} ({ai.confidence:.0%})\n"
            f"Expires: {expires_at.isoformat()}\n\n"
            "This approval affects the local paper ledger only. No broker order will be sent."
        )
