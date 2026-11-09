"""Telegram commands for the demo bots.

One `goldbot telegram-commands` process polls Telegram for all sessions, so any number of bots
can run without competing for getUpdates. It never trades on its own: demo /pause and /resume
write each session's control.json.
/closeall (after an inline confirmation) asks Capital.com to close positions listed in demo
session ledgers; each demo bot then records the closes through its normal reconciliation.

Only messages from TELEGRAM_ALLOWED_USER_ID in TELEGRAM_CHAT_ID are obeyed. Commands sent
while this process was down are skipped rather than replayed.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .capital import CapitalAPIError, CapitalDemoClient
from .control import read_control, read_session_info, write_control
from .health import PowerMonitor
from .stats import format_summary, summarize
from .telegram import TelegramAPIError, TelegramBotClient


COMMANDS = [
    ("status", "Bots, open positions and trends"),
    ("stats", "Closed-trade performance per bot"),
    ("pause", "Stop new entries: /pause [bot|all]"),
    ("resume", "Allow new entries: /resume [bot|all]"),
    ("closeall", "Close owned positions: /closeall <bot|lab|daytrade|all>"),
    ("lab", "Strategy lab results, one line per strategy"),
    ("daytrade", "Day-trading portfolio: one line per rule"),
    ("daily", "Today's summary (also sent automatically at 22:00)"),
    ("help", "List commands"),
]
HELP = (
    "Goldbot commands\n"
    "/status: bots, open positions, trends, today's P&L\n"
    "/stats: closed-trade performance per bot\n"
    "/pause [bot|all]: stop demo entries (open trades are still managed)\n"
    "/resume [bot|all]: allow new entries again\n"
    "/closeall <bot|lab|daytrade|all>: close owned broker positions and pause entries (asks to confirm)\n"
    "/lab: strategy lab results; /pause lab and /resume lab pause the whole lab\n"
    "/daytrade: day-trading portfolio per rule; /pause daytrade and /resume daytrade pause all its rules\n"
    "/daily: today's summary (sent automatically every day at 22:00 Swiss time)\n"
    "Without a bot name, /pause and /resume apply to all sessions."
)
STALE_AFTER = timedelta(minutes=2)
REPORT_ZONE = ZoneInfo("Europe/Zurich")
CONFIRM_TTL = timedelta(minutes=2)



# Multi-strategy processes: one directory per strategy under one data folder.
GROUPS = {"lab": ("🧪", "Strategy lab", "strategies"), "daytrade": ("📈", "Day-trading portfolio", "rules")}


def process_running(session: str) -> bool:
    pattern = f"goldbot {session}$" if session in GROUPS else f"goldbot watch --session {session} "
    result = subprocess.run(
        ["pgrep", "-f", pattern],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


@dataclass
class _PendingClose:
    sessions: list[str]
    expires_at: datetime


class CommandService:
    def __init__(
        self,
        client: TelegramBotClient,
        *,
        chat_id: str,
        allowed_user_id: int,
        sessions_dir: Path,
        stopped_dir: Path,
        state_path: Path,
        capital_factory: Callable[[], CapitalDemoClient] | None = None,
        lab_dir: Path | None = None,
        daytrade_dir: Path | None = None,
        logs_dir: Path | None = None,
        report_hour: int = 22,
        is_running: Callable[[str], bool] = process_running,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.client = client
        self.chat_id = str(chat_id)
        self.allowed_user_id = int(allowed_user_id)
        self.sessions_dir = sessions_dir
        self.stopped_dir = stopped_dir
        self.state_path = state_path
        self.capital_factory = capital_factory
        self.lab_dir = lab_dir
        self.daytrade_dir = daytrade_dir
        self.logs_dir = logs_dir
        self.report_hour = report_hour
        self.is_running = is_running
        self.clock = clock
        self.pending: dict[str, _PendingClose] = {}
        self._persisted: dict[str, Any] = self._load_state()
        self.offset: int | None = self._load_offset()

    # ----- polling -------------------------------------------------------------------------

    def start(self) -> None:
        """Register the command menu and skip any backlog from while this process was down."""
        self.client.set_my_commands(COMMANDS)
        if "error_lines" not in self._persisted:
            # First run: only errors that appear from now on should raise alerts.
            self._new_log_errors(update_baseline=True)
            self._save_state()
        if self.offset is None:
            backlog = self.client.get_updates(offset=-1, limit=1, timeout=0)
            self._save_offset(backlog[-1]["update_id"] + 1 if backlog else 0)

    def poll(self, *, timeout: int = 25) -> list[dict[str, Any]]:
        updates = self.client.get_updates(offset=self.offset, timeout=timeout)
        handled = []
        for update in updates:
            self._save_offset(int(update["update_id"]) + 1)
            try:
                event = self.handle(update)
            except (CapitalAPIError, TelegramAPIError, OSError, ValueError, KeyError) as exc:
                event = {"event": "COMMAND_ERROR", "error": str(exc)}
                self._reply(f"⚠️ Command failed: {exc}")
            if event:
                handled.append(event)
        return handled

    def handle(self, update: dict[str, Any]) -> dict[str, Any] | None:
        if "callback_query" in update:
            return self._handle_callback(update["callback_query"])
        message = update.get("message") or {}
        text = str(message.get("text") or "").strip()
        if not text.startswith("/"):
            return None
        if not self._authorized(message.get("chat"), message.get("from")):
            return {"event": "COMMAND_UNAUTHORIZED", "from": (message.get("from") or {}).get("id")}
        sent = datetime.fromtimestamp(int(message.get("date") or 0), tz=timezone.utc)
        if self.clock() - sent > STALE_AFTER:
            return {"event": "COMMAND_STALE", "text": text}

        command, *arguments = text.split()
        command = command.split("@", 1)[0].lstrip("/").lower()
        argument = " ".join(arguments) if arguments else None
        if command in ("start", "help"):
            self._reply(HELP)
        elif command == "status":
            self._reply(self.status_text())
        elif command == "stats":
            self._reply(self.stats_text())
        elif command in ("pause", "resume"):
            self._reply(self._set_paused(argument, paused=command == "pause"))
        elif command == "closeall":
            self._ask_close(argument)
        elif command == "lab":
            self._reply(self.lab_text())
        elif command == "daytrade":
            self._reply(self.group_text("daytrade"))
        elif command == "daily":
            self._reply(self.daily_text())
        else:
            self._reply(f"Unknown command /{command}.\n\n{HELP}")
        return {"event": "COMMAND", "command": command, "argument": argument}

    # ----- commands ------------------------------------------------------------------------

    def sessions(self) -> list[str]:
        if not self.sessions_dir.exists():
            return []
        return sorted(path.name for path in self.sessions_dir.iterdir() if path.is_dir())

    def status_text(self) -> str:
        sessions = self.sessions()
        if not sessions:
            return "No bot sessions found."
        prices: dict[str, dict[str, float] | None] = {}
        blocks = []
        for name in sessions:
            directory = self.sessions_dir / name
            info = read_session_info(directory)
            state = self._state(directory)
            if self.is_running(name):
                paused = read_control(directory).paused
                heading = f"{'⏸' if paused else '▶'} {name}: running" + (
                    ", NEW ENTRIES PAUSED" if paused else ""
                )
            elif (self.stopped_dir / name).exists():
                heading = f"⏹ {name}: stopped on purpose"
            else:
                heading = f"⚠️ {name}: NOT running"
            lines = [heading]
            if info:
                filters = info.get("trend_resolution") or "none"
                if info.get("confirm_resolution"):
                    filters += f" + {info['confirm_resolution']}"
                lines.append(
                    f"{info.get('resolution')} entries · filter {filters} · "
                    f"{info.get('account_name') or 'default account'}"
                )
            decision = self._last_decision(directory)
            if decision:
                result = decision.get("result") or {}
                trends = f"trend {result.get('higher_timeframe_bias')}"
                if result.get("confirm_bias") not in (None, "NOT_USED"):
                    trends += f", confirm {result['confirm_bias']}"
                setup = result.get("setup") or {}
                lines.append(
                    f"Last candle {str(decision.get('timestamp', ''))[11:16]} UTC: {trends} → "
                    f"{setup.get('decision')}"
                )
            if state:
                lines.append(
                    f"Today: {state.get('trades_today', 0)} trades, "
                    f"local USD {float(state.get('daily_realized_pnl') or 0):+.2f}"
                )
                epic = str(info.get("epic") or "GOLD")
                if state.get("open_positions") and epic not in prices:
                    prices[epic] = self._prices(epic)
                for position in state.get("open_positions") or []:
                    lines.append(self._position_line(position, prices.get(epic)))
            blocks.append("\n".join(lines))
        if self.group_entries("daytrade"):
            blocks.append(self._group_status("daytrade", prices))
        return "\n\n".join(blocks)

    def _group_status(self, name: str, prices: dict[str, dict[str, float] | None]) -> str:
        icon, title, noun = GROUPS[name]
        entries = self.group_entries(name)
        running = self.is_running(name)
        paused = [path.name for path in entries if read_control(path).paused]
        if running:
            heading = f"{icon} {title}: running" + (f", PAUSED: {', '.join(paused)}" if paused else "")
        elif (self.stopped_dir / name).exists():
            heading = f"⏹ {title}: stopped on purpose"
        else:
            heading = f"⚠️ {title}: NOT running"
        lines = [heading]
        trades = sum(int(self._state(path).get("trades_today") or 0) for path in entries)
        lines.append(f"{len(entries)} {noun} · today {trades} trade(s)")
        for path in entries:
            state = self._state(path)
            epic = str(read_session_info(path).get("epic") or "GOLD")
            for position in state.get("open_positions") or []:
                if epic not in prices:
                    prices[epic] = self._prices(epic)
                lines.append(f"{path.name}: " + self._position_line(position, prices.get(epic)))
        return "\n".join(lines)

    def stats_text(self) -> str:
        sessions = [
            name for name in self.sessions() if (self.sessions_dir / name / "journal.jsonl").exists()
        ]
        if not sessions:
            return "No closed-trade history yet."
        return "\n\n".join(format_summary(summarize(self.sessions_dir / name)) for name in sessions)

    def lab_entries(self) -> list[Path]:
        return self.group_entries("lab")

    def group_entries(self, name: str) -> list[Path]:
        root = {"lab": self.lab_dir, "daytrade": self.daytrade_dir}.get(name)
        if root is None or not root.exists():
            return []
        return sorted(path for path in root.iterdir() if path.is_dir())

    def lab_text(self) -> str:
        return self.group_text("lab")

    def group_text(self, name: str) -> str:
        icon, title, noun = GROUPS[name]
        entries = self.group_entries(name)
        if not entries:
            return f"The {title.lower()} has no {noun} yet."
        running = self.is_running(name)
        paused = any(read_control(path).paused for path in entries)
        lines = [
            f"{icon} {title}: {'running' if running else 'NOT running'}"
            + (", new entries PAUSED" if paused else "")
        ]
        total_trades = 0
        total_r = 0.0
        for path in entries:
            summary = summarize(path)
            trades = summary["trades"]
            total_trades += trades
            open_count = len(summary["open_positions"])
            if trades:
                r_sum = summary["average_r"] * trades
                total_r += r_sum
                broker = " ".join(
                    f"{currency} {values['net_pnl']:+.2f}"
                    for currency, values in summary.get("broker_pnl", {}).items()
                )
                lines.append(
                    f"{path.name}: {trades} tr, {summary['win_rate']:.0%} win, "
                    f"{summary['average_r']:+.2f}R avg, {r_sum:+.1f}R"
                    + (f", {broker}" if broker else "")
                    + (f", {open_count} open" if open_count else "")
                )
            else:
                lines.append(f"{path.name}: no closed trades" + (f", {open_count} open" if open_count else ""))
        lines.append(f"Total: {total_trades} closed trades, {total_r:+.1f}R")
        return "\n".join(lines)

    def _set_paused(self, argument: str | None, *, paused: bool) -> str:
        normalized = (argument or "all").lower()
        if (argument or "").lower() in GROUPS:
            group = (argument or "").lower()
            icon, title, noun = GROUPS[group]
            entries = self.group_entries(group)
            if not entries:
                return f"The {title.lower()} has no {noun} yet."
            for path in entries:
                write_control(path, paused=paused, changed_by="telegram")
            verb = "paused: no new entries" if paused else "resumed: new entries allowed"
            return f"{'⏸' if paused else '▶'} {title} ({len(entries)} {noun}) {verb}."
        targets = self._targets(argument or "all")
        if isinstance(targets, str):
            return targets
        for name in targets:
            write_control(self.sessions_dir / name, paused=paused, changed_by="telegram")
        verb = "paused: no new entries" if paused else "resumed: new entries allowed"
        note = "\nOpen trades are still managed (stops, trailing, targets)." if paused else ""
        return f"{'⏸' if paused else '▶'} {', '.join(targets)} {verb}.{note}"

    def _ask_close(self, argument: str | None) -> None:
        if not argument:
            self._reply("Say which bot: /closeall <bot> or /closeall all.")
            return
        targets = self._close_targets(argument)
        if isinstance(targets, str):
            self._reply(targets)
            return
        lines = []
        for name in targets:
            directories = self._target_directories(name)
            position_count = sum(
                len(self._state(directory).get("open_positions") or [])
                for directory in directories
            )
            suffix = f" across {len(directories)} {GROUPS[name][2]}" if name in GROUPS else ""
            lines.append(f"{name}: {position_count} open position(s){suffix}")
        token = uuid4().hex[:10]
        self.pending[token] = _PendingClose(targets, self.clock() + CONFIRM_TTL)
        self.client.send_message(
            self.chat_id,
            "⚠️ Close ALL broker positions and pause new entries for:\n"
            + "\n".join(lines)
            + "\n\nThis expires in 2 minutes.",
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": "Close positions", "callback_data": f"closeall:{token}"},
                        {"text": "Cancel", "callback_data": f"cancel:{token}"},
                    ]
                ]
            },
        )

    def _handle_callback(self, callback: dict[str, Any]) -> dict[str, Any] | None:
        message = callback.get("message") or {}
        data = str(callback.get("data") or "")
        action, _, token = data.partition(":")
        if action not in ("closeall", "cancel"):
            return None  # e.g. paper-approval buttons from another mode
        if not self._authorized(message.get("chat"), callback.get("from")):
            self.client.answer_callback_query(callback["id"], "Not authorized.", show_alert=True)
            return {"event": "COMMAND_UNAUTHORIZED", "from": (callback.get("from") or {}).get("id")}
        pending = self.pending.pop(token, None)
        if message.get("message_id") is not None:
            try:
                self.client.remove_inline_keyboard(self.chat_id, int(message["message_id"]))
            except TelegramAPIError:
                pass
        if pending is None or self.clock() > pending.expires_at:
            self.client.answer_callback_query(callback["id"], "Expired. Send /closeall again.")
            return {"event": "CLOSEALL_EXPIRED"}
        if action == "cancel":
            self.client.answer_callback_query(callback["id"], "Cancelled.")
            self._reply("Cancelled. Nothing was closed.")
            return {"event": "CLOSEALL_CANCELLED"}
        self.client.answer_callback_query(callback["id"], "Closing…")
        report = self._close_sessions(pending.sessions)
        self._reply(report)
        return {"event": "CLOSEALL", "sessions": pending.sessions}

    def _close_sessions(self, sessions: list[str]) -> str:
        lines = []
        for name in sessions:
            lines.extend(self._close_target(name))
        lines.append("The bots record the closes and P&L on their next check (~15 s).")
        return "\n".join(lines)

    def _close_target(self, name: str) -> list[str]:
        directories = self._target_directories(name)
        for directory in directories:
            write_control(directory, paused=True, changed_by="telegram /closeall")

        owned: list[tuple[str, str]] = []
        for directory in directories:
            owner = f"{name}:{directory.name}" if name in GROUPS else name
            owned.extend(
                (owner, str(position["deal_id"]))
                for position in self._state(directory).get("open_positions") or []
                if position.get("deal_id")
            )
        if not owned:
            noun = f"{len(directories)} {GROUPS[name][2]}" if name in GROUPS else "session"
            return [f"{name}: no broker positions to close; {noun} paused."]
        if self.capital_factory is None:
            return [f"{name}: Capital.com is not configured; paused only."]

        account_ids = {
            str(account_id)
            for directory in directories
            if (account_id := read_session_info(directory).get("account_id"))
        }
        if name in GROUPS and len(account_ids) != 1:
            return [
                f"{name}: account metadata is missing or inconsistent; positions were not closed, "
                f"but all {name} entries are paused."
            ]

        lines: list[str] = []
        try:
            with self.capital_factory() as capital:
                if account_ids:
                    capital.use_account(next(iter(account_ids)))
                broker_ids: dict[str, str] = {}
                for item in capital.get_positions():
                    position = item.get("position") or {}
                    if position.get("dealId"):
                        broker_id = str(position["dealId"])
                        broker_ids[broker_id] = broker_id
                        if position.get("workingOrderId"):
                            broker_ids[str(position["workingOrderId"])] = broker_id

                attempted: set[str] = set()
                for owner, deal_id in owned:
                    broker_id = broker_ids.get(deal_id)
                    if broker_id is None:
                        lines.append(f"{owner}: {deal_id[-6:]} already closed at the broker.")
                        continue
                    if broker_id in attempted:
                        lines.append(f"{owner}: {broker_id[-6:]} close already sent.")
                        continue
                    attempted.add(broker_id)
                    try:
                        reference = capital.close_position(broker_id)
                        status = capital.get_confirmation(reference).get("dealStatus")
                    except (CapitalAPIError, OSError, ValueError, KeyError) as exc:
                        lines.append(f"{owner}: {broker_id[-6:]} close FAILED: {exc}.")
                    else:
                        lines.append(f"{owner}: {broker_id[-6:]} close {status or 'sent'}.")
        except (CapitalAPIError, OSError, ValueError, KeyError) as exc:
            lines.append(f"{name}: broker lookup FAILED: {exc}.")

        lines.append(f"{name}: paused. /resume {name} to allow new entries again.")
        return lines

    # ----- helpers -------------------------------------------------------------------------

    def _targets(self, argument: str) -> list[str] | str:
        sessions = self.sessions()
        if argument.lower() == "all":
            return sessions or "No bot sessions found."
        if argument in sessions:
            return [argument]
        return f"Unknown bot {argument!r}. Bots: {', '.join(sessions) or 'none'}."

    def _close_targets(self, argument: str) -> list[str] | str:
        sessions = self.sessions()
        groups = [name for name in GROUPS if self.group_entries(name)]
        normalized = argument.lower()
        if normalized == "all":
            targets = sessions + groups
            return targets or "No bot sessions or lab entries found."
        if normalized in GROUPS:
            return [normalized] if normalized in groups else f"The {GROUPS[normalized][1].lower()} has no entries yet."
        if argument in sessions:
            return [argument]
        available = sessions + groups
        return f"Unknown bot {argument!r}. Bots: {', '.join(available) or 'none'}."

    def _target_directories(self, name: str) -> list[Path]:
        return self.group_entries(name) if name in GROUPS else [self.sessions_dir / name]

    def _authorized(self, chat: Any, sender: Any) -> bool:
        return (
            str((chat or {}).get("id")) == self.chat_id
            and (sender or {}).get("id") == self.allowed_user_id
        )

    def _prices(self, epic: str) -> dict[str, float] | None:
        if self.capital_factory is None:
            return None
        try:
            with self.capital_factory() as capital:
                snapshot = capital.get_market_details(epic).get("snapshot") or {}
            return {"bid": float(snapshot["bid"]), "offer": float(snapshot["offer"])}
        except (CapitalAPIError, KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _position_line(position: dict[str, Any], prices: dict[str, float] | None) -> str:
        direction = position["direction"]
        entry = float(position["entry"])
        line = (
            f"Open: {direction} {position['units']:g} @ {entry:.2f} "
            f"(stop {float(position['stop']):.2f}, target {float(position['target']):.2f})"
        )
        if prices:
            sign = 1.0 if direction == "BUY" else -1.0
            price = prices["bid"] if direction == "BUY" else prices["offer"]
            points = (price - entry) * sign
            risk = abs(entry - float(position.get("initial_stop") or position["stop"]))
            line += f" → {points:+.2f} pts" + (f" ({points / risk:+.2f}R)" if risk else "")
        return line

    @staticmethod
    def _state(directory: Path) -> dict[str, Any]:
        try:
            payload = json.loads((directory / "paper_state.json").read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _last_decision(directory: Path) -> dict[str, Any] | None:
        journal = directory / "journal.jsonl"
        if not journal.exists():
            return None
        with journal.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 262_144))
            tail = stream.read().decode("utf-8", errors="ignore").splitlines()
        for line in reversed(tail):
            if '"event":"DECISION"' in line:
                try:
                    return json.loads(line)
                except ValueError:
                    continue
        return None

    def notify(self, text: str) -> None:
        """Send an unsolicited message (health warnings) to the user's chat."""
        self._reply(text)

    def _reply(self, text: str) -> None:
        self.client.send_message(self.chat_id, text[:4000])

    def _load_state(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._persisted, sort_keys=True), encoding="utf-8")
        temporary.replace(self.state_path)

    def _load_offset(self) -> int | None:
        try:
            return int(self._persisted["offset"])
        except (KeyError, TypeError, ValueError):
            return None

    def _save_offset(self, offset: int) -> None:
        self.offset = offset
        self._persisted["offset"] = offset
        self._save_state()

    # ----- daily summary --------------------------------------------------------------------

    def maybe_send_daily(self) -> bool:
        """Send the daily summary once per local day, at or after the report time."""
        now = self.clock().astimezone(REPORT_ZONE)
        if now.hour < self.report_hour:
            return False
        today = now.date().isoformat()
        if self._persisted.get("last_daily") == today:
            return False
        self._reply(self.daily_text(update_error_baseline=True))
        self._persisted["last_daily"] = today
        self._save_state()
        return True

    def daily_text(self, *, update_error_baseline: bool = False) -> str:
        now = self.clock().astimezone(REPORT_ZONE)
        today = now.date()
        lines = [f"📅 Daily summary {today:%a %d %b} ({now:%H:%M} Swiss time)"]
        alerts: list[str] = []
        total_trades, total_r, total_chf, total_fees = 0, 0.0, 0.0, 0.0

        groups = [(name, self.sessions_dir / name, False) for name in self.sessions()]
        groups += [(f"lab:{path.name}", path, "lab") for path in self.lab_entries()]
        groups += [(f"daytrade:{path.name}", path, "daytrade") for path in self.group_entries("daytrade")]
        group_totals = {name: [0, 0.0, 0] for name in GROUPS}
        for label, directory, is_lab in groups:
            name = directory.name
            closes = self._closes_on(directory, today)
            state = self._state(directory)
            open_count = len(state.get("open_positions") or [])
            r_sum = sum(self._trade_r(event) for event in closes)
            chf = sum(float(e["broker_pnl"]) for e in closes if e.get("broker_pnl") is not None)
            total_trades += len(closes)
            total_r += r_sum
            total_chf += chf
            total_fees += self._fees_on(directory, today)
            if is_lab:
                group_totals[is_lab][0] += len(closes)
                group_totals[is_lab][1] += r_sum
                group_totals[is_lab][2] += open_count
                continue
            if self.is_running(name):
                status = "⏸" if read_control(directory).paused else "▶"
            elif (self.stopped_dir / name).exists():
                continue  # stopped on purpose: not part of the daily report
            else:
                status = "⚠️"
                alerts.append(f"{name} is NOT running")
            wins = sum(1 for e in closes if float(e.get("pnl", 0)) > 0)
            line = f"{status} {name}: "
            line += (
                f"{len(closes)} closed ({wins}W/{len(closes) - wins}L), {r_sum:+.1f}R"
                + (f", CHF {chf:+.2f}" if any(e.get("broker_pnl") is not None for e in closes) else "")
                if closes else "no closed trades"
            )
            if open_count:
                line += f", {open_count} open"
            lines.append(line)
        for group, (g_trades, g_r, g_open) in group_totals.items():
            entries = self.group_entries(group)
            if not entries:
                continue
            running = self.is_running(group)
            if not running and not (self.stopped_dir / group).exists():
                alerts.append(f"{group} is NOT running")
            lines.append(
                f"{'▶' if running else '⚠️'} {group} ({len(entries)} {GROUPS[group][2]}): "
                + (f"{g_trades} closed, {g_r:+.1f}R" if g_trades else "no closed trades")
                + (f", {g_open} open" if g_open else "")
            )
        lines.append(f"Total today: {total_trades} closed trades, {total_r:+.1f}R, broker CHF {total_chf:+.2f}")
        if total_fees:
            lines.append(
                f"Overnight fees today: CHF {total_fees:+.2f} (net CHF {total_chf + total_fees:+.2f})"
            )

        errors = self._new_log_errors(update_baseline=update_error_baseline)
        for name, count in errors.items():
            alerts.append(f"{name}: {count} new error line(s) in its log")
        if alerts:
            lines += ["", "⚠️ Needs attention:"] + [f"- {alert}" for alert in alerts]
        else:
            lines += ["", "✅ No alerts: all bots running, no new errors."]
        return "\n".join(lines)

    @staticmethod
    def _trade_r(event: dict[str, Any]) -> float:
        position = event["position"]
        risk = abs(float(position["entry"]) - float(position.get("initial_stop") or position["stop"]))
        sign = 1.0 if position["direction"] == "BUY" else -1.0
        return (float(event["exit_price"]) - float(position["entry"])) * sign / risk if risk else 0.0

    @staticmethod
    def _fees_on(directory: Path, day: Any) -> float:
        """This session's share of broker overnight fees booked on `day` (Swiss date)."""
        journal = directory / "journal.jsonl"
        if not journal.exists():
            return 0.0
        total = 0.0
        with journal.open(encoding="utf-8", errors="ignore") as stream:
            for line in stream:
                if "FINANCING" not in line:
                    continue
                try:
                    event = json.loads(line)
                    booked = datetime.fromisoformat(str(event["timestamp"]))
                except (ValueError, KeyError):
                    continue
                if booked.tzinfo is None:
                    booked = booked.replace(tzinfo=timezone.utc)
                if event.get("event") == "FINANCING" and booked.astimezone(REPORT_ZONE).date() == day:
                    total += float(event.get("amount") or 0.0)
        return total

    @staticmethod
    def _closes_on(directory: Path, day: Any) -> list[dict[str, Any]]:
        journal = directory / "journal.jsonl"
        if not journal.exists():
            return []
        closes = []
        with journal.open(encoding="utf-8", errors="ignore") as stream:
            for line in stream:
                if '"event":"PAPER_CLOSE"' not in line:
                    continue
                try:
                    event = json.loads(line)
                    closed = datetime.fromisoformat(str(event["timestamp"]))
                except (ValueError, KeyError):
                    continue
                if closed.tzinfo is None:
                    closed = closed.replace(tzinfo=timezone.utc)
                if closed.astimezone(REPORT_ZONE).date() == day:
                    closes.append(event)
        return closes

    def _new_log_errors(self, *, update_baseline: bool) -> dict[str, int]:
        if self.logs_dir is None or not self.logs_dir.exists():
            return {}
        baseline = self._persisted.setdefault("error_lines", {})
        found = {}
        for log in sorted(self.logs_dir.glob("watcher-*.log")):
            name = log.stem.removeprefix("watcher-")
            count = 0
            with log.open(encoding="utf-8", errors="ignore") as stream:
                for line in stream:
                    if '"error"' in line or "POLL_ERROR" in line or "Traceback" in line:
                        count += 1
            new = count - int(baseline.get(name, 0))
            if new > 0:
                found[name] = new
            if update_baseline:
                baseline[name] = count
        return found


def run_forever(
    service: CommandService,
    *,
    log: Callable[[object], None],
    monitor: PowerMonitor | None = None,
) -> None:
    service.start()
    monitor = monitor or PowerMonitor()
    log({"event": "COMMANDS_STARTED", "sessions": service.sessions()})
    while True:
        try:
            for event in service.poll():
                log(event)
            if service.maybe_send_daily():
                log({"event": "DAILY_SUMMARY_SENT"})
            for text in monitor.check():
                service.notify(text)
                log({"event": "HEALTH_ALERT", "text": text})
        except TelegramAPIError as exc:
            log({"event": "COMMANDS_POLL_ERROR", "error": str(exc)})
            time.sleep(5)
