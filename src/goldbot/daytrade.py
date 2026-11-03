"""Issue #166 Part C: the day-trading portfolio, one demo account with one lane per passed intraday rule.

This module holds the pure parts: the roster, edge-based sizing and the Telegram texts. The polling loop lives in
`cli._daytrade_command`. Everything here is pre-registered on #166 (comment of 27 Oct): base risk 1%, daily loss
limit −2%, open-risk cap 3%, and the edge sizing rule below.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DAYTRADE_EPICS = ("GOLD", "US100", "US500")
ZURICH = ZoneInfo("Europe/Zurich")


@dataclass(frozen=True)
class DayLane:
    name: str
    epic: str
    strategy: str
    resolution: str
    prior_r: float                       # μ0 = 0.5 × the Part A backtest average R
    flat_at: str                         # "<zone> HH:MM", every lane closes daily
    window: tuple[str, str, str]         # (zone, start, end) shown in the morning message
    trend_resolution: str | None = None
    entry_orders: str = "market"
    trail: bool = False
    max_trades_per_day: int = 1
    max_spread: float = 0.80
    min_rr: float = 1.5
    hours: str = "all"
    price_count: int = 300


@dataclass(frozen=True)
class DayRoster:
    account: str
    lanes: tuple[DayLane, ...]
    risk_per_trade: float = 0.01
    max_size: float = 100.0
    max_account_open_risk: float = 0.03
    max_account_daily_loss: float = 0.02
    prior_trades: int = 50
    min_trades: int = 20
    min_multiplier: float = 0.25
    max_multiplier: float = 1.25
    pause_after: int = 40
    poll_seconds: int = 15
    starting_balance: float = 10000.0
    notes: dict[str, Any] = field(default_factory=dict)


def load_roster(path: Path) -> DayRoster:
    payload = json.loads(path.read_text(encoding="utf-8"))
    lanes = []
    for item in payload["lanes"]:
        item = {**item, "window": tuple(item["window"])}
        lane = DayLane(**item)
        if lane.epic not in DAYTRADE_EPICS:
            raise ValueError(f"Day-trading lane {lane.name!r}: epic {lane.epic!r} is not allowed")
        if lane.entry_orders not in ("market", "stop"):
            raise ValueError(f"Day-trading lane {lane.name!r}: entry_orders must be market or stop")
        if lane.entry_orders == "stop" and not lane.strategy.startswith("rangebreak-"):
            raise ValueError(f"Day-trading lane {lane.name!r}: stop orders need a rangebreak strategy")
        if not lane.flat_at or len(lane.flat_at.split()) != 2:
            raise ValueError(f"Day-trading lane {lane.name!r}: flat_at must look like 'Europe/London 16:00'")
        if lane.prior_r <= 0:
            raise ValueError(f"Day-trading lane {lane.name!r}: prior_r must be positive")
        lanes.append(lane)
    names = [lane.name for lane in lanes]
    if len(set(names)) != len(names):
        raise ValueError("Day-trading lane names must be unique")
    keys = {k: payload[k] for k in ("risk_per_trade", "max_size", "max_account_open_risk", "max_account_daily_loss",
                                    "prior_trades", "min_trades", "min_multiplier", "max_multiplier", "pause_after",
                                    "poll_seconds", "starting_balance") if k in payload}
    roster = DayRoster(account=str(payload["account"]), lanes=tuple(lanes), notes=dict(payload.get("notes", {})), **keys)
    if not 0 < roster.risk_per_trade * roster.max_multiplier <= 0.02:
        raise ValueError("Day-trading risk per trade times the largest multiplier must stay at or below 2%")
    if not 0 < roster.max_account_daily_loss <= 0.10 or not 0 < roster.max_account_open_risk <= 0.10:
        raise ValueError("Day-trading account limits must be between 0 and 10%")
    return roster


# ----------------------------------------------------------------------------------------------- edge sizing
def closed_r(journal: Path) -> list[float]:
    """R of every closed position in a lane's journal (the bot's own trades only)."""
    out = []
    try:
        lines = journal.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return out
    for line in lines:
        if '"PAPER_CLOSE"' not in line:
            continue
        try:
            e = json.loads(line)
            p = e["position"]
            risk = abs(float(p["entry"]) - float(p.get("initial_stop", p["stop"])))
            sign = 1.0 if p["direction"] == "BUY" else -1.0
            if risk > 0:
                out.append((float(e["exit_price"]) - float(p["entry"])) * sign / risk)
        except (ValueError, KeyError, TypeError):
            continue
    return out


def edge(rs: list[float], prior_r: float, roster: DayRoster) -> dict[str, Any]:
    """Pre-registered (#166): μ = (n0·μ0 + ΣR)/(n0 + n); multiplier 1 below min_trades, then clamp(μ/μ0); pause at μ ≤ 0."""
    n = len(rs)
    mu = (roster.prior_trades * prior_r + sum(rs)) / (roster.prior_trades + n)
    if n >= roster.pause_after and mu <= 0:
        return {"n": n, "posterior_r": round(mu, 4), "multiplier": 0.0, "paused": True}
    if n < roster.min_trades:
        mult = 1.0
    else:
        mult = min(roster.max_multiplier, max(roster.min_multiplier, mu / prior_r))
    return {"n": n, "posterior_r": round(mu, 4), "multiplier": round(mult, 3), "paused": False}


# ----------------------------------------------------------------------------------------------- Telegram texts
def _local_window(lane: DayLane, day: date) -> str:
    zone, start, end = lane.window
    tz = ZoneInfo(zone)
    def at(clock: str) -> str:
        h, m = (int(x) for x in clock.split(":"))
        return datetime(day.year, day.month, day.day, h, m, tzinfo=tz).astimezone(ZURICH).strftime("%H:%M")
    return f"{at(start)}–{at(end)}"


def morning_text(roster: DayRoster, day: date, edges: dict[str, dict], balance: float | None,
                 yesterday: dict[str, Any]) -> str:
    lines = [f"☀️ Day-trading portfolio, {day.strftime('%a %d %b')}"]
    if balance is not None:
        lines.append(f"Account CHF {balance:,.0f} · daily limit −{roster.max_account_daily_loss:.0%} "
                     f"(CHF {balance * roster.max_account_daily_loss:,.0f}) · open-risk cap {roster.max_account_open_risk:.0%}")
    for lane in roster.lanes:
        e = edges.get(lane.name, {})
        state = "⏸ paused" if e.get("paused") else f"×{e.get('multiplier', 1.0):.2f}"
        lines.append(f"• {lane.name} {_local_window(lane, day)} ({state}, {e.get('n', 0)} trades)")
    if yesterday.get("trades"):
        lines.append(f"Yesterday: {yesterday['trades']} trades, {yesterday['r']:+.2f}R, CHF {yesterday['chf']:+,.0f}")
    return "\n".join(lines)


def evening_text(roster: DayRoster, day: date, today: dict[str, Any], balance: float | None, blocked: bool) -> str:
    lines = [f"🌙 Day-trading portfolio, {day.strftime('%a %d %b')}"]
    if not today.get("trades"):
        lines.append("No trades today.")
    for lane, row in sorted(today.get("lanes", {}).items()):
        lines.append(f"• {lane}: {row['trades']} trade(s), {row['r']:+.2f}R, CHF {row['chf']:+,.0f}")
    if today.get("trades"):
        pct = f" ({today['chf'] / balance:+.2%})" if balance else ""
        lines.append(f"Total: {today['r']:+.2f}R, CHF {today['chf']:+,.0f}{pct}")
    if blocked:
        lines.append(f"The −{roster.max_account_daily_loss:.0%} daily limit was reached; entries stopped for the day.")
    return "\n".join(lines)


def day_summary(lane_dirs: dict[str, Path], day: date) -> dict[str, Any]:
    """Closed trades per lane on a Zurich calendar day."""
    out = {"trades": 0, "r": 0.0, "chf": 0.0, "lanes": {}}
    for name, d in lane_dirs.items():
        try:
            lines = (d / "journal.jsonl").read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        for line in lines:
            if '"PAPER_CLOSE"' not in line:
                continue
            try:
                e = json.loads(line)
                if datetime.fromisoformat(e["timestamp"]).astimezone(ZURICH).date() != day:
                    continue
                p = e["position"]
                risk = abs(float(p["entry"]) - float(p.get("initial_stop", p["stop"])))
                sign = 1.0 if p["direction"] == "BUY" else -1.0
                r = (float(e["exit_price"]) - float(p["entry"])) * sign / risk if risk else 0.0
                chf = float(e.get("broker_pnl") if e.get("broker_pnl") is not None else e.get("pnl") or 0.0)
            except (ValueError, KeyError, TypeError):
                continue
            row = out["lanes"].setdefault(name, {"trades": 0, "r": 0.0, "chf": 0.0})
            row["trades"] += 1; row["r"] += r; row["chf"] += chf
            out["trades"] += 1; out["r"] += r; out["chf"] += chf
    return out


class MessageClock:
    """Sends the morning (~08:00) and evening (~22:15) Zurich messages once per weekday; state survives restarts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.state = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            self.state = {}

    def due(self, kind: str, now: datetime) -> bool:
        local = now.astimezone(ZURICH)
        if local.weekday() >= 5:
            return False
        start, end = ((7, 55), (9, 0)) if kind == "morning" else ((22, 15), (23, 30))
        if not start <= (local.hour, local.minute) < end:
            return False
        return self.state.get(kind) != local.date().isoformat()

    def mark(self, kind: str, now: datetime) -> None:
        self.state[kind] = now.astimezone(ZURICH).date().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state), encoding="utf-8")
        tmp.replace(self.path)
