from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from goldbot.capital import CapitalAPIError
from goldbot.commands import CommandService
from goldbot.control import read_control, write_session_info


NOW = datetime(2026, 9, 29, 16, 0, tzinfo=timezone.utc)
CHAT = "123"
USER = 42


class FakeTelegram:
    def __init__(self, updates: list[dict[str, Any]] | None = None) -> None:
        self.updates = updates or []
        self.sent: list[dict[str, Any]] = []
        self.answers: list[str] = []
        self.commands: list[tuple[str, str]] = []

    def set_my_commands(self, commands: list[tuple[str, str]]) -> None:
        self.commands = commands

    def get_updates(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.updates

    def send_message(self, chat_id: str, text: str, *, reply_markup: Any = None) -> dict[str, Any]:
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return {"message_id": len(self.sent)}

    def answer_callback_query(self, callback_id: str, text: str, *, show_alert: bool = False) -> None:
        self.answers.append(text)

    def remove_inline_keyboard(self, chat_id: str, message_id: int) -> None:
        pass


class FakeCapital:
    def __init__(self) -> None:
        self.account: str | None = None
        self.account_calls: list[str] = []
        self.closed: list[str] = []
        self.enter_count = 0
        self.positions = [
            {"position": {"dealId": "pos-1", "workingOrderId": "order-1"}, "market": {"epic": "GOLD"}}
        ]

    def __enter__(self) -> "FakeCapital":
        self.enter_count += 1
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def use_account(self, account: str) -> dict[str, Any]:
        self.account = account
        self.account_calls.append(account)
        return {}

    def get_positions(self) -> list[dict[str, Any]]:
        return self.positions

    def close_position(self, deal_id: str) -> str:
        self.closed.append(deal_id)
        return f"p_{deal_id}"

    def get_confirmation(self, reference: str) -> dict[str, Any]:
        return {"dealStatus": "ACCEPTED"}

    def get_market_details(self, epic: str) -> dict[str, Any]:
        return {"snapshot": {"bid": 102.0, "offer": 102.3}}


def message(text: str, *, user: int = USER, chat: str = CHAT, age: timedelta = timedelta()) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "text": text,
            "chat": {"id": int(chat)},
            "from": {"id": user},
            "date": int((NOW - age).timestamp()),
        },
    }


def service(tmp_path: Path, telegram: FakeTelegram, *, running: set[str] | None = None,
            capital: FakeCapital | None = None) -> CommandService:
    sessions = tmp_path / "sessions"
    for name in ("1h", "5m"):
        (sessions / name).mkdir(parents=True, exist_ok=True)
    return CommandService(
        telegram,  # type: ignore[arg-type]
        chat_id=CHAT,
        allowed_user_id=USER,
        sessions_dir=sessions,
        stopped_dir=tmp_path / "stopped",
        state_path=tmp_path / "commands_state.json",
        capital_factory=(lambda: capital) if capital else None,  # type: ignore[return-value]
        is_running=lambda name: name in (running if running is not None else {"1h"}),
        clock=lambda: NOW,
    )




def test_ignores_commands_from_other_users_and_chats(tmp_path) -> None:
    telegram = FakeTelegram()
    commands = service(tmp_path, telegram)

    assert commands.handle(message("/pause", user=7))["event"] == "COMMAND_UNAUTHORIZED"
    assert commands.handle(message("/pause", chat="999"))["event"] == "COMMAND_UNAUTHORIZED"
    assert telegram.sent == []
    assert not read_control(tmp_path / "sessions" / "1h").paused


def test_skips_commands_sent_while_the_process_was_down(tmp_path) -> None:
    telegram = FakeTelegram()
    commands = service(tmp_path, telegram)

    event = commands.handle(message("/pause", age=timedelta(minutes=5)))

    assert event["event"] == "COMMAND_STALE"
    assert not read_control(tmp_path / "sessions" / "1h").paused


def test_start_registers_menu_and_skips_backlog(tmp_path) -> None:
    telegram = FakeTelegram([{"update_id": 41}])
    commands = service(tmp_path, telegram)

    commands.start()

    assert commands.offset == 42
    assert json.loads((tmp_path / "commands_state.json").read_text())["offset"] == 42
    assert ("status", "Bots, open positions and trends") in telegram.commands


def test_pause_and_resume_one_bot_or_all(tmp_path) -> None:
    telegram = FakeTelegram()
    commands = service(tmp_path, telegram)

    commands.handle(message("/pause 1h"))
    assert read_control(tmp_path / "sessions" / "1h").paused
    assert not read_control(tmp_path / "sessions" / "5m").paused

    commands.handle(message("/pause"))
    assert read_control(tmp_path / "sessions" / "5m").paused

    commands.handle(message("/resume all"))
    assert not read_control(tmp_path / "sessions" / "1h").paused
    assert "resumed" in telegram.sent[-1]["text"]

    commands.handle(message("/pause nope"))
    assert "Unknown bot 'nope'" in telegram.sent[-1]["text"]












def test_closeall_needs_confirmation_then_closes_and_pauses(tmp_path) -> None:
    telegram = FakeTelegram()
    capital = FakeCapital()
    commands = service(tmp_path, telegram, capital=capital)
    directory = tmp_path / "sessions" / "1h"
    write_session_info(directory, {"account_id": "acct-1h"})
    (directory / "paper_state.json").write_text(json.dumps({
        "open_positions": [{"direction": "BUY", "entry": 100, "stop": 98, "target": 104,
                            "units": 0.1, "deal_id": "order-1"}],
    }))

    commands.handle(message("/closeall 1h"))

    assert capital.closed == []  # nothing happens before confirmation
    buttons = telegram.sent[-1]["reply_markup"]["inline_keyboard"][0]
    confirm = buttons[0]["callback_data"]
    callback = {"callback_query": {"id": "cb", "data": confirm, "from": {"id": USER},
                                   "message": {"chat": {"id": int(CHAT)}, "message_id": 1}}}

    event = commands.handle(callback)

    assert event["event"] == "CLOSEALL"
    assert capital.account == "acct-1h"
    assert capital.closed == ["pos-1"]  # order ID mapped to the broker position ID
    assert read_control(directory).paused
    assert "pos-1"[-6:] in telegram.sent[-1]["text"]

    # The same button cannot be used twice.
    assert commands.handle(callback)["event"] == "CLOSEALL_EXPIRED"
    assert capital.closed == ["pos-1"]


def test_closeall_rejects_other_users_and_cancel_closes_nothing(tmp_path) -> None:
    telegram = FakeTelegram()
    capital = FakeCapital()
    commands = service(tmp_path, telegram, capital=capital)
    commands.handle(message("/closeall 1h"))
    buttons = telegram.sent[-1]["reply_markup"]["inline_keyboard"][0]

    def press(data: str, user: int) -> dict[str, Any]:
        return {"callback_query": {"id": "cb", "data": data, "from": {"id": user},
                                   "message": {"chat": {"id": int(CHAT)}, "message_id": 1}}}

    assert commands.handle(press(buttons[0]["callback_data"], 7))["event"] == "COMMAND_UNAUTHORIZED"
    assert commands.handle(press(buttons[1]["callback_data"], USER))["event"] == "CLOSEALL_CANCELLED"
    assert capital.closed == []


def test_closeall_lab_closes_only_owned_positions_with_one_account_session(tmp_path) -> None:
    telegram = FakeTelegram()
    capital = FakeCapital()
    capital.positions = [
        {"position": {"dealId": "pos-1", "workingOrderId": "order-1"}},
        {"position": {"dealId": "pos-2", "workingOrderId": "order-2"}},
        {"position": {"dealId": "not-owned", "workingOrderId": "other-order"}},
    ]
    commands = service(tmp_path, telegram, capital=capital)
    lab = tmp_path / "lab"
    commands.lab_dir = lab
    for name, deal_id in (("gold-zone", "order-1"), ("us100-orb", "pos-2")):
        directory = lab / name
        directory.mkdir(parents=True)
        write_session_info(directory, {"account_id": "acct-lab", "account_name": "lab"})
        (directory / "paper_state.json").write_text(json.dumps({
            "open_positions": [{"deal_id": deal_id}],
        }))

    commands.handle(message("/closeall lab"))

    prompt = telegram.sent[-1]
    assert "lab: 2 open position(s) across 2 strategies" in prompt["text"]
    confirm = prompt["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    event = commands.handle({
        "callback_query": {
            "id": "cb-lab", "data": confirm, "from": {"id": USER},
            "message": {"chat": {"id": int(CHAT)}, "message_id": 2},
        }
    })

    assert event == {"event": "CLOSEALL", "sessions": ["lab"]}
    assert capital.enter_count == 1
    assert capital.account_calls == ["acct-lab"]
    assert capital.closed == ["pos-1", "pos-2"]
    assert "not-owned" not in capital.closed
    assert all(read_control(lab / name).paused for name in ("gold-zone", "us100-orb"))
    assert "lab:gold-zone" in telegram.sent[-1]["text"]
    assert "lab:us100-orb" in telegram.sent[-1]["text"]


def test_closeall_all_includes_and_pauses_flat_lab(tmp_path) -> None:
    telegram = FakeTelegram()
    capital = FakeCapital()
    commands = service(tmp_path, telegram, capital=capital)
    lab = tmp_path / "lab"
    commands.lab_dir = lab
    for name in ("gold-zone", "us100-orb"):
        (lab / name).mkdir(parents=True)

    commands.handle(message("/closeall all"))

    prompt = telegram.sent[-1]
    assert "lab: 0 open position(s) across 2 strategies" in prompt["text"]
    confirm = prompt["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    event = commands.handle({
        "callback_query": {
            "id": "cb-all", "data": confirm, "from": {"id": USER},
            "message": {"chat": {"id": int(CHAT)}, "message_id": 3},
        }
    })

    assert event == {"event": "CLOSEALL", "sessions": ["1h", "5m", "lab"]}
    assert all(read_control(lab / name).paused for name in ("gold-zone", "us100-orb"))
    assert capital.enter_count == 0
    assert "lab: no broker positions to close; 2 strategies paused" in telegram.sent[-1]["text"]


def test_closeall_lab_continues_after_one_position_fails(tmp_path) -> None:
    class PartlyFailingCapital(FakeCapital):
        def close_position(self, deal_id: str) -> str:
            if deal_id == "pos-1":
                raise CapitalAPIError("rejected")
            return super().close_position(deal_id)

    telegram = FakeTelegram()
    capital = PartlyFailingCapital()
    capital.positions = [
        {"position": {"dealId": "pos-1"}},
        {"position": {"dealId": "pos-2"}},
    ]
    commands = service(tmp_path, telegram, capital=capital)
    lab = tmp_path / "lab"
    commands.lab_dir = lab
    for name, deal_id in (("first", "pos-1"), ("second", "pos-2")):
        directory = lab / name
        directory.mkdir(parents=True)
        write_session_info(directory, {"account_id": "acct-lab"})
        (directory / "paper_state.json").write_text(json.dumps({
            "open_positions": [{"deal_id": deal_id}],
        }))

    commands.handle(message("/closeall lab"))
    confirm = telegram.sent[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    commands.handle({
        "callback_query": {
            "id": "cb-partial", "data": confirm, "from": {"id": USER},
            "message": {"chat": {"id": int(CHAT)}, "message_id": 4},
        }
    })

    report = telegram.sent[-1]["text"]
    assert "lab:first: pos-1 close FAILED: rejected" in report
    assert "lab:second: pos-2 close ACCEPTED" in report
    assert capital.closed == ["pos-2"]
    assert all(read_control(lab / name).paused for name in ("first", "second"))



def test_status_prices_positions_in_their_own_market(tmp_path) -> None:
    class Prices(FakeCapital):
        def __init__(self) -> None:
            super().__init__()
            self.asked: list[str] = []

        def get_market_details(self, epic: str) -> dict:
            self.asked.append(epic)
            return {"snapshot": {"bid": 30300.0, "offer": 30301.8}}

    telegram = FakeTelegram()
    capital = Prices()
    commands = service(tmp_path, telegram, capital=capital)
    directory = tmp_path / "sessions" / "1h"
    write_session_info(directory, {"epic": "US100"})
    (directory / "paper_state.json").write_text(json.dumps({"open_positions": [
        {"direction": "BUY", "entry": 30280.0, "stop": 30240.0, "target": 30360.0, "units": 0.006,
         "initial_stop": 30240.0},
    ]}))

    commands.handle(message("/status"))

    assert capital.asked == ["US100"]
    assert "+20.00 pts (+0.50R)" in telegram.sent[-1]["text"]


def test_lab_summary_and_pause_all_lab_entries(tmp_path) -> None:
    telegram = FakeTelegram()
    commands = service(tmp_path, telegram)
    lab = tmp_path / "lab"
    for name in ("us100-orb15", "us30-orb15"):
        (lab / name).mkdir(parents=True)
    position = {"direction": "BUY", "entry": 100.0, "stop": 98.0, "target": 104.0, "units": 1.0,
                "strategy": "orb-nycash-15-rr2", "initial_stop": 98.0}
    (lab / "us100-orb15" / "journal.jsonl").write_text(
        json.dumps({"event": "PAPER_CLOSE", "pnl": 4.0, "position": position,
                    "broker_pnl": 3.6, "broker_currency": "CHF"}) + "\n")
    commands.lab_dir = lab
    commands.is_running = lambda name: name == "lab"

    commands.handle(message("/lab"))
    text = telegram.sent[-1]["text"]
    assert text.startswith("🧪 Strategy lab: running")
    assert "us100-orb15: 1 tr, 100% win, +2.00R avg, +2.0R, CHF +3.60" in text
    assert "us30-orb15: no closed trades" in text

    commands.handle(message("/pause lab"))
    assert all(read_control(lab / name).paused for name in ("us100-orb15", "us30-orb15"))
    assert not read_control(tmp_path / "sessions" / "1h").paused
    commands.handle(message("/resume lab"))
    assert not any(read_control(lab / name).paused for name in ("us100-orb15", "us30-orb15"))


def daily_service(tmp_path, telegram, *, now, running=("1h", "lab")):  # type: ignore[no-untyped-def]
    commands = CommandService(
        telegram,  # type: ignore[arg-type]
        chat_id=CHAT, allowed_user_id=USER,
        sessions_dir=tmp_path / "sessions", stopped_dir=tmp_path / "stopped",
        state_path=tmp_path / "commands_state.json", lab_dir=tmp_path / "lab",
        logs_dir=tmp_path / "logs", is_running=lambda name: name in running, clock=lambda: now,
    )
    return commands


def close_event(when: str, pnl: float, broker: float | None = None) -> str:
    position = {"direction": "SELL", "entry": 4144.45, "stop": 4148.8, "initial_stop": 4148.8,
                "target": 4137.72, "units": 21.19, "strategy": "ema_pullback"}
    exit_price = 4137.72 if pnl > 0 else 4148.8
    event = {"event": "PAPER_CLOSE", "timestamp": when, "exit_price": exit_price, "pnl": pnl,
             "position": position}
    if broker is not None:
        event.update(broker_pnl=broker, broker_currency="CHF")
    return json.dumps(event, separators=(",", ":")) + "\n"


def test_daily_summary_counts_todays_closes_and_flags_problems(tmp_path) -> None:
    now = datetime(2026, 9, 29, 20, 5, tzinfo=timezone.utc)  # 22:05 in Zurich
    for name in ("1h", "5m-learn", "5m"):
        (tmp_path / "sessions" / name).mkdir(parents=True)
    (tmp_path / "stopped").mkdir()
    (tmp_path / "stopped" / "5m").touch()
    (tmp_path / "lab" / "us100-orb15").mkdir(parents=True)
    (tmp_path / "sessions" / "5m-learn" / "journal.jsonl").write_text(
        close_event("2026-09-29T18:10:00+00:00", 142.6, 131.2)
        + close_event("2026-09-29T19:00:00+00:00", -92.2, -85.0)
        + close_event("2026-09-28T19:00:00+00:00", -92.2)  # yesterday: not counted
    )
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "watcher-5m-learn.log").write_text('{"error": "timeout"}\n')
    telegram = FakeTelegram()
    commands = daily_service(tmp_path, telegram, now=now)

    text = commands.daily_text()

    assert text.startswith("📅 Daily summary Tue 29 Sep (22:05 Swiss time)")
    assert "5m-learn: 2 closed (1W/1L), +0.5R, CHF +46.20" in text
    assert "▶ 1h: no closed trades" in text
    assert "5m:" not in text                      # stopped on purpose: left out
    assert "⚠️ 5m-learn" in text and "- 5m-learn is NOT running" in text
    assert "- 5m-learn: 1 new error line(s) in its log" in text
    assert "lab (1 strategies): no closed trades" in text
    assert "Total today: 2 closed trades, +0.5R, broker CHF +46.20" in text


def test_daily_summary_is_sent_once_per_day_after_22_even_across_restarts(tmp_path) -> None:
    (tmp_path / "sessions" / "1h").mkdir(parents=True)
    telegram = FakeTelegram()
    early = datetime(2026, 9, 29, 19, 50, tzinfo=timezone.utc)   # 21:50 Zurich
    late = datetime(2026, 9, 29, 20, 1, tzinfo=timezone.utc)     # 22:01 Zurich

    assert daily_service(tmp_path, telegram, now=early).maybe_send_daily() is False
    assert daily_service(tmp_path, telegram, now=late).maybe_send_daily() is True
    # A restart (new service instance) on the same evening must not send it again.
    assert daily_service(tmp_path, telegram, now=late).maybe_send_daily() is False
    assert len(telegram.sent) == 1
    next_day = datetime(2026, 9, 30, 20, 1, tzinfo=timezone.utc)
    assert daily_service(tmp_path, telegram, now=next_day).maybe_send_daily() is True


def test_error_alerts_only_report_new_errors_since_the_last_summary(tmp_path) -> None:
    (tmp_path / "logs").mkdir(parents=True)
    log = tmp_path / "logs" / "watcher-1h.log"
    log.write_text('{"error": "a"}\n{"error": "b"}\n')
    telegram = FakeTelegram()
    late = datetime(2026, 9, 29, 20, 1, tzinfo=timezone.utc)
    daily_service(tmp_path, telegram, now=late).maybe_send_daily()
    assert "1h: 2 new error line(s)" in telegram.sent[-1]["text"]

    commands = daily_service(tmp_path, telegram, now=late)
    assert "new error line(s)" not in commands.daily_text()
    log.write_text(log.read_text() + '{"error": "c"}\n')
    assert "1h: 1 new error line(s)" in commands.daily_text()


def test_daily_command_and_offset_survive_in_one_state_file(tmp_path) -> None:
    telegram = FakeTelegram([{"update_id": 9}])
    commands = daily_service(tmp_path, telegram, now=NOW)
    commands.start()
    commands.handle(message("/daily"))
    assert telegram.sent[-1]["text"].startswith("📅 Daily summary")
    assert json.loads((tmp_path / "commands_state.json").read_text())["offset"] == 10
    assert daily_service(tmp_path, telegram, now=NOW).offset == 10


def test_first_start_does_not_alert_on_old_log_errors(tmp_path) -> None:
    (tmp_path / "logs").mkdir(parents=True)
    (tmp_path / "logs" / "watcher-1h.log").write_text('{"error": "old"}\n')
    telegram = FakeTelegram()
    commands = daily_service(tmp_path, telegram, now=NOW)
    commands.start()
    assert "new error line(s)" not in commands.daily_text()


def _daytrade(tmp_path, commands, deal_ids=("order-1",)):
    root = tmp_path / "daytrade"
    commands.daytrade_dir = root
    for k, name in enumerate(("gold-london", "us100-orb")):
        d = root / name
        d.mkdir(parents=True)
        write_session_info(d, {"account_id": "acct-dt", "account_name": "Day-Trading Portfolio", "epic": "GOLD"})
        positions = [{"deal_id": deal_ids[k], "direction": "BUY", "entry": 100.0, "stop": 95.0, "target": 120.0,
                      "units": 1.0}] if k < len(deal_ids) else []
        (d / "paper_state.json").write_text(json.dumps({"open_positions": positions, "trades_today": 1}))
    return root


def test_daytrade_pause_resume_and_text(tmp_path) -> None:
    telegram = FakeTelegram()
    commands = service(tmp_path, telegram)
    root = _daytrade(tmp_path, commands)
    commands.handle(message("/pause daytrade"))
    assert "Day-trading portfolio (2 rules) paused" in telegram.sent[-1]["text"]
    assert all(read_control(root / n).paused for n in ("gold-london", "us100-orb"))
    commands.handle(message("/daytrade"))
    assert telegram.sent[-1]["text"].startswith("📈 Day-trading portfolio")
    commands.handle(message("/resume daytrade"))
    assert not read_control(root / "gold-london").paused


def test_status_and_daily_include_the_daytrade_portfolio(tmp_path) -> None:
    telegram = FakeTelegram()
    commands = service(tmp_path, telegram, capital=FakeCapital())
    _daytrade(tmp_path, commands)
    text = commands.status_text()
    assert "Day-trading portfolio" in text and "2 rules · today 2 trade(s)" in text and "gold-london:" in text
    assert "daytrade (2 rules)" in commands.daily_text()


def test_closeall_daytrade_closes_only_its_owned_positions(tmp_path) -> None:
    telegram = FakeTelegram()
    capital = FakeCapital()
    capital.positions = [{"position": {"dealId": "pos-1", "workingOrderId": "order-1"}},
                         {"position": {"dealId": "not-owned"}}]
    commands = service(tmp_path, telegram, capital=capital)
    root = _daytrade(tmp_path, commands)
    commands.handle(message("/closeall daytrade"))
    assert "daytrade: 1 open position(s) across 2 rules" in telegram.sent[-1]["text"]
    confirm = telegram.sent[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    event = commands.handle({"callback_query": {"id": "cb-dt", "data": confirm, "from": {"id": USER},
                                                "message": {"chat": {"id": int(CHAT)}, "message_id": 4}}})
    assert event == {"event": "CLOSEALL", "sessions": ["daytrade"]}
    assert capital.account_calls == ["acct-dt"] and capital.closed == ["pos-1"]
    assert all(read_control(root / n).paused for n in ("gold-london", "us100-orb"))
