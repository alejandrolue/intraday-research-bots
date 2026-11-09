from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .models import Candle, MarketSetup
from .risk import RiskDecision, RiskState


def _new_id() -> str:
    return uuid4().hex[:8]


@dataclass
class PaperPosition:
    direction: Literal["BUY", "SELL"]
    entry: float
    stop: float
    target: float
    units: float
    opened_at: str
    strategy: str = "unspecified"
    deal_id: str | None = None
    local_id: str = field(default_factory=_new_id)
    initial_stop: float | None = None
    role: Literal["signal", "add"] = "signal"
    # Broker fills that closed part of the position before its final close (issue #70):
    # units, sum of level x units, exit reasons, broker P&L, and the activity/transaction keys
    # already counted, so the final close combines everything exactly once.
    partials: dict[str, Any] = field(default_factory=dict)
    # Overnight financing allocated to this position, in account currency (issue #44).
    fees: float = 0.0
    # Market state when the position was opened (issue #77), for later analysis.
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.initial_stop is None:
            self.initial_stop = self.stop

    @property
    def risk_points(self) -> float:
        assert self.initial_stop is not None
        return abs(self.entry - self.initial_stop)

    def profit_r(self, price: float) -> float:
        """Open profit at `price` in multiples of the initial risk."""
        if self.risk_points <= 0:
            return 0.0
        sign = 1.0 if self.direction == "BUY" else -1.0
        return (price - self.entry) * sign / self.risk_points


@dataclass
class PaperState:
    balance: float
    daily_realized_pnl: float
    trades_today: int
    trading_day: str
    last_processed_timestamp: str | None = None
    open_positions: list[PaperPosition] = field(default_factory=list)
    # Broker SWAP (overnight fee) references this session has already allocated.
    fee_references: list[str] = field(default_factory=list)


class PaperLedger:
    def __init__(
        self,
        state_path: Path,
        journal_path: Path,
        *,
        starting_balance: float,
        point_value_per_unit: float,
    ) -> None:
        self.state_path = state_path
        self.journal_path = journal_path
        self.starting_balance = starting_balance
        self.point_value_per_unit = point_value_per_unit
        # Issue #198: the demo account's broker balance, set by the bot loop. When present it
        # replaces this ledger's own balance for sizing and the daily-loss limit; the ledger
        # balance keeps tracking this session's own P&L.
        self.sizing_balance: float | None = None
        self.state = self._load()

    @property
    def open_positions(self) -> list[PaperPosition]:
        return self.state.open_positions

    def position(self, local_id: str) -> PaperPosition:
        for position in self.state.open_positions:
            if position.local_id == local_id:
                return position
        raise KeyError(f"No open paper position {local_id!r}")

    def risk_state(self, now: datetime) -> RiskState:
        self._roll_day(now.date())
        positions = self.state.open_positions
        return RiskState(
            balance=self.sizing_balance if self.sizing_balance and self.sizing_balance > 0 else self.state.balance,
            daily_realized_pnl=self.state.daily_realized_pnl,
            trades_today=self.state.trades_today,
            open_positions=len(positions),
            open_direction=positions[0].direction if positions else None,
            open_risk=sum(self._position_open_risk(position) for position in positions),
        )

    def _position_open_risk(self, position: PaperPosition) -> float:
        sign = 1.0 if position.direction == "BUY" else -1.0
        risk_points = max(0.0, (position.entry - position.stop) * sign)
        return risk_points * position.units * self.point_value_per_unit

    def already_processed(self, timestamp: datetime) -> bool:
        return self.state.last_processed_timestamp == timestamp.isoformat()

    def mark_processed(self, timestamp: datetime) -> None:
        self.state.last_processed_timestamp = timestamp.isoformat()
        self._save()

    def update_open_positions(self, candle: Candle) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        # Broker-managed positions are closed only by reconciling with the broker.
        for position in [item for item in self.state.open_positions if item.deal_id is None]:
            if position.direction == "BUY":
                stop_hit = candle.low <= position.stop
                target_hit = candle.high >= position.target
            else:
                stop_hit = candle.high >= position.stop
                target_hit = candle.low <= position.target
            if not stop_hit and not target_hit:
                continue
            # If both were touched inside one candle, assume the adverse fill first.
            events.append(
                self.close_position(
                    position.local_id,
                    exit_price=position.stop if stop_hit else position.target,
                    exit_reason="STOP" if stop_hit else "TARGET",
                    timestamp=candle.timestamp.isoformat(),
                )
            )
        return events

    def close_position(
        self,
        local_id: str,
        *,
        exit_price: float,
        exit_reason: str,
        timestamp: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        position = self.position(local_id)
        multiplier = 1.0 if position.direction == "BUY" else -1.0
        pnl = (
            (exit_price - position.entry)
            * multiplier
            * position.units
            * self.point_value_per_unit
        )
        self.state.balance += pnl
        self.state.daily_realized_pnl += pnl
        event = {
            "event": "PAPER_CLOSE",
            "timestamp": timestamp,
            "exit_reason": exit_reason,
            "exit_price": exit_price,
            "pnl": pnl,
            "balance": self.state.balance,
            "daily_realized_pnl": self.state.daily_realized_pnl,
            "open_positions_left": len(self.state.open_positions) - 1,
            "position": asdict(position),
            **(extra or {}),
        }
        self.state.open_positions.remove(position)
        self._append(event)
        self._save()
        return event

    def open_position(
        self,
        setup: MarketSetup,
        risk: RiskDecision,
        *,
        deal_id: str | None = None,
        role: Literal["signal", "add"] = "signal",
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not risk.approved or not setup.ready or setup.direction is None:
            raise ValueError("Only approved READY setups can open a paper position")
        if any(item.direction != setup.direction for item in self.state.open_positions):
            raise RuntimeError("Open paper positions are in the opposite direction")
        assert setup.entry is not None and setup.stop is not None and setup.target is not None
        position = PaperPosition(
            direction=setup.direction,
            entry=setup.entry,
            stop=setup.stop,
            target=setup.target,
            units=risk.units,
            opened_at=setup.timestamp.isoformat(),
            strategy=setup.strategy,
            deal_id=deal_id,
            role=role,
            context=dict(context or {}),
        )
        self.state.open_positions.append(position)
        self.state.trades_today += 1
        event = {
            "event": "PAPER_OPEN",
            "timestamp": setup.timestamp.isoformat(),
            "risk_budget": risk.risk_budget,
            "open_positions": len(self.state.open_positions),
            "position": asdict(position),
        }
        self._append(event)
        self._save()
        return event

    def record_financing(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Journal this session's share of a broker overnight fee (no paper balance change)."""
        event = {"event": "FINANCING", **payload}
        self.state.fee_references.append(str(payload["reference"]))
        self._append(event)
        self._save()
        return event

    def record_partial_close(self, local_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Journal a broker fill that closed part of a still-open position (no balance change)."""
        position = self.position(local_id)
        event = {"event": "PAPER_PARTIAL_CLOSE", "position": asdict(position), **payload}
        self._append(event)
        self._save()
        return event

    def set_deal_id(self, local_id: str, deal_id: str) -> None:
        self.position(local_id).deal_id = deal_id
        self._save()

    def update_levels(
        self,
        local_id: str,
        *,
        stop: float,
        target: float,
        timestamp: str,
        reason: str,
    ) -> dict[str, Any]:
        position = self.position(local_id)
        event = {
            "event": "LEVELS_UPDATED",
            "timestamp": timestamp,
            "reason": reason,
            "old_stop": position.stop,
            "old_target": position.target,
            "stop": stop,
            "target": target,
        }
        position.stop = stop
        position.target = target
        event["position"] = asdict(position)
        self._append(event)
        self._save()
        return event

    def record_decision(self, payload: dict[str, Any]) -> None:
        self._append({"event": "DECISION", **payload})

    def record_event(self, payload: dict[str, Any]) -> None:
        """Append operational metadata without changing the paper account state."""
        self._append(payload)

    def _load(self) -> PaperState:
        today = datetime.now(timezone.utc).date().isoformat()
        if not self.state_path.exists():
            return PaperState(self.starting_balance, 0.0, 0, today)
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        # Older state files held a single optional position.
        legacy = payload.pop("open_position", None)
        positions = payload.pop("open_positions", None) or ([legacy] if legacy else [])
        state = PaperState(**payload)
        state.open_positions = [PaperPosition(**item) for item in positions]
        return state

    def _roll_day(self, current_day: date) -> None:
        current = current_day.isoformat()
        if self.state.trading_day != current:
            self.state.trading_day = current
            self.state.daily_realized_pnl = 0.0
            self.state.trades_today = 0
            self._save()

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self.state)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.state_path)

    def _append(self, payload: dict[str, Any]) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
