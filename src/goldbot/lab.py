"""Strategy lab: many candidate strategies forward-tested on one demo account (issue #24).

The roster lives in a committed JSON file. Every entry gets its own ledger under
`data/lab/<name>/`, trades its own deal IDs on the shared demo account and is judged by the
promotion rule pre-registered in issue #24. `PollCache` makes all entries share one price,
market and positions fetch per poll, so a large roster stays inside Capital.com's rate limits.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .paper import PaperLedger
from .session_strategies import SessionVariant
from .structure_strategies import StructureVariant
from .ict_strategies import SilverBulletVariant, TurtleSoupVariant
from .sessions import resolution_delta


# Markets the lab may trade, on the lab demo account only. Main sessions use DEMO_EPICS.
LAB_EPICS = ("GOLD", "US100", "US30", "US500", "J225", "HK50")
BUILT_IN = ("breakout_retest", "ema_pullback", "demand_zone_rejection")


@dataclass(frozen=True)
class LabEntry:
    name: str
    epic: str
    strategies: tuple[str, ...]
    resolution: str = "MINUTE_5"
    trend_resolution: str | None = "HOUR_4"
    confirm_resolution: str | None = None
    max_positions: int = 1
    max_trades_per_day: int = 3
    manage: bool = False
    hours: str = "london-ny"
    max_spread: float = 0.80
    min_rr: float = 1.5
    flat_at: str | None = None          # "<zone> HH:MM": no entries and own positions closed until midnight (#191)
    source: str = ""


@dataclass(frozen=True)
class LabRoster:
    account: str
    entries: tuple[LabEntry, ...]
    poll_seconds: int = 20
    starting_balance: float = 1000.0
    risk_per_trade: float = 0.0025
    max_size: float = 0.1
    max_daily_loss: float = 0.01
    max_open_positions: int = 40
    max_account_open_risk: float = 0.05
    max_account_daily_loss: float = 0.10
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LabRiskSnapshot:
    account_balance: float
    open_risk: float
    open_risk_limit: float
    broker_pnl_today: float
    estimated_pnl_today: float
    daily_loss_limit: float

    @property
    def remaining_open_risk(self) -> float:
        return max(0.0, self.open_risk_limit - self.open_risk)

    @property
    def daily_loss_blocked(self) -> bool:
        return self.account_pnl_today <= -self.daily_loss_limit

    @property
    def account_pnl_today(self) -> float:
        return self.broker_pnl_today + self.estimated_pnl_today

    def to_dict(self) -> dict[str, float | bool]:
        return {
            "account_balance": self.account_balance,
            "open_risk": self.open_risk,
            "open_risk_limit": self.open_risk_limit,
            "remaining_open_risk": self.remaining_open_risk,
            "broker_pnl_today": self.broker_pnl_today,
            "estimated_pnl_today": self.estimated_pnl_today,
            "account_pnl_today": self.account_pnl_today,
            "daily_loss_limit": self.daily_loss_limit,
            "daily_loss_blocked": self.daily_loss_blocked,
        }


def load_roster(path: Path) -> LabRoster:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    for item in payload["entries"]:
        values = {**item, "strategies": tuple(item["strategies"])}
        entry = LabEntry(**values)
        _validate(entry)
        entries.append(entry)
    names = [entry.name for entry in entries]
    if len(set(names)) != len(names):
        raise ValueError("Lab entry names must be unique")
    roster = LabRoster(
        account=str(payload["account"]),
        entries=tuple(entries),
        poll_seconds=int(payload.get("poll_seconds", 20)),
        starting_balance=float(payload.get("starting_balance", 1000.0)),
        risk_per_trade=float(payload.get("risk_per_trade", 0.0025)),
        max_size=float(payload.get("max_size", 0.1)),
        max_daily_loss=float(payload.get("max_daily_loss", 0.01)),
        max_open_positions=int(payload.get("max_open_positions", 40)),
        max_account_open_risk=float(payload.get("max_account_open_risk", 0.05)),
        max_account_daily_loss=float(payload.get("max_account_daily_loss", 0.10)),
        notes=dict(payload.get("notes", {})),
    )
    if not 0 < roster.max_account_open_risk <= 1:
        raise ValueError("Lab max_account_open_risk must be between 0 and 1")
    if not 0 < roster.max_account_daily_loss <= 1:
        raise ValueError("Lab max_account_daily_loss must be between 0 and 1")
    return roster


def account_balance(accounts: list[dict[str, Any]], account_id: str) -> float:
    matches = [item for item in accounts if str(item.get("accountId")) == str(account_id)]
    if len(matches) != 1:
        raise ValueError(f"No single Capital.com account matches lab account ID {account_id!r}")
    raw = matches[0].get("balance")
    if isinstance(raw, dict):
        raw = raw.get("balance")
    try:
        balance = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Lab account balance is unavailable") from exc
    if balance <= 0:
        raise ValueError("Lab account balance must be positive")
    return balance


def risk_snapshot(
    ledgers: list[PaperLedger],
    *,
    account_balance_value: float,
    trading_day: date,
    max_open_risk: float,
    max_daily_loss: float,
) -> LabRiskSnapshot:
    open_risk = sum(
        max(
            0.0,
            (position.entry - position.stop)
            * (1.0 if position.direction == "BUY" else -1.0),
        )
        * position.units
        * ledger.point_value_per_unit
        for ledger in ledgers
        for position in ledger.open_positions
    )
    pnl_parts = [_account_pnl_for_day(ledger, trading_day) for ledger in ledgers]
    broker_pnl = sum(item[0] for item in pnl_parts)
    estimated_pnl = sum(item[1] for item in pnl_parts)
    return LabRiskSnapshot(
        account_balance=account_balance_value,
        open_risk=open_risk,
        open_risk_limit=account_balance_value * max_open_risk,
        broker_pnl_today=broker_pnl,
        estimated_pnl_today=estimated_pnl,
        daily_loss_limit=account_balance_value * max_daily_loss,
    )


def _account_pnl_for_day(ledger: PaperLedger, trading_day: date) -> tuple[float, float]:
    journal = ledger.journal_path
    if not journal.exists():
        return 0.0, 0.0
    broker_total = 0.0
    estimated_total = 0.0
    with journal.open(encoding="utf-8", errors="ignore") as stream:
        for line in stream:
            try:
                event = json.loads(line)
                if event.get("event") != "PAPER_CLOSE":
                    continue
                timestamp = datetime.fromisoformat(str(event["timestamp"]).replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                if timestamp.astimezone(timezone.utc).date() == trading_day:
                    if event.get("broker_pnl") is not None:
                        broker_total += float(event["broker_pnl"])
                        continue
                    # Broker history can lag or fail after a close. Count a local loss, but
                    # never credit an unverified local profit toward the account safety limit.
                    if event.get("pnl") is not None:
                        estimated_total += min(0.0, float(event["pnl"]))
                        continue
                    position = event.get("position") or {}
                    entry = float(position["entry"])
                    initial_stop = float(position.get("initial_stop") or position["stop"])
                    units = float(position["units"])
                    estimated_total -= (
                        abs(entry - initial_stop) * units * ledger.point_value_per_unit
                    )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
    return broker_total, estimated_total


def _validate(entry: LabEntry) -> None:
    if not entry.name.replace("-", "").replace("_", "").replace(".", "").isalnum():
        raise ValueError(f"Lab entry name {entry.name!r} may contain only letters, digits, - _ .")
    if entry.epic not in LAB_EPICS:
        raise ValueError(f"Lab entry {entry.name!r}: market {entry.epic} is not in LAB_EPICS")
    for resolution in (entry.resolution, entry.trend_resolution, entry.confirm_resolution):
        if resolution:
            resolution_delta(resolution)
    if entry.hours not in ("london-ny", "all"):
        raise ValueError(f"Lab entry {entry.name!r}: hours must be london-ny or all")
    for name in entry.strategies:
        if name.startswith("structure_"):
            StructureVariant.parse(name)
        elif name.startswith("silverbullet-"):
            SilverBulletVariant.parse(name)
        elif name.startswith("turtlesoup-"):
            TurtleSoupVariant.parse(name)
        elif name not in BUILT_IN:
            SessionVariant.parse(name)
    if entry.flat_at:
        from .cli import parse_flat_at   # lazy: cli imports this module

        parse_flat_at(entry.flat_at)
    if not 1.0 <= entry.min_rr <= 5.0:
        raise ValueError(f"Lab entry {entry.name!r}: min_rr must be between 1.0 and 5.0")
    if entry.max_positions < 1 or entry.max_trades_per_day < 1:
        raise ValueError(f"Lab entry {entry.name!r}: limits must be positive")


class PollCache:
    """Share read calls between lab entries within one poll; writes invalidate the cache."""

    _CACHED = ("get_prices", "get_positions", "get_market_details", "get_activity",
               "get_accounts",
               "get_transactions", "get_preferences")
    _WRITES = ("create_position", "update_position", "close_position")

    def __init__(self, client: Any) -> None:
        self._client = client
        self._cache: dict[tuple, Any] = {}

    def new_poll(self) -> None:
        self._cache.clear()

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._client, name)
        if name in self._CACHED:
            def cached(*args: Any, **kwargs: Any) -> Any:
                key = (name, args, tuple(sorted(kwargs.items())))
                if key not in self._cache:
                    self._cache[key] = target(*args, **kwargs)
                return self._cache[key]
            return cached
        if name in self._WRITES:
            def write(*args: Any, **kwargs: Any) -> Any:
                result = target(*args, **kwargs)
                # Positions and quotes change after a write; history may gain the new deal.
                for key in [key for key in self._cache if key[0] != "get_prices"]:
                    del self._cache[key]
                return result
            return write
        return target
