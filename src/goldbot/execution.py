from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from .capital import CapitalAPIError, CapitalDemoClient
from .management import ManagementConfig, plan_levels, pyramid_ready, stop_protects_entry
from .models import MarketSetup
from .paper import PaperLedger, PaperPosition
from .risk import RiskDecision


ExecutionStatus = Literal["BLOCKED", "REJECTED", "OPENED"]


@dataclass(frozen=True)
class DemoExecutionResult:
    status: ExecutionStatus
    reason: str
    epic: str
    strategy: str | None = None
    size: float | None = None
    deal_reference: str | None = None
    deal_id: str | None = None
    paper_event: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DemoOrderExecutor:
    """Submit and manage guarded positions on Capital.com's hard-coded demo API."""

    def __init__(
        self,
        client: CapitalDemoClient,
        ledger: PaperLedger,
        journal_path: Path,
        *,
        allowed_epic: str = "GOLD",
        max_size: float = 0.1,
        confirmation_attempts: int = 5,
        confirmation_delay: float = 0.5,
        max_entry_drift_r: float = 0.5,
        close_lookup_attempts: int = 8,
        max_open_positions: int = 6,
        hedging_mode: bool = False,
        management: ManagementConfig | None = None,
        use_minimum_size: bool = False,
    ) -> None:
        if max_size <= 0:
            raise ValueError("Capital.com maximum demo size must be positive")
        self.client = client
        self.ledger = ledger
        self.journal_path = journal_path
        self.allowed_epic = allowed_epic
        self.max_size = max_size
        self.confirmation_attempts = confirmation_attempts
        self.confirmation_delay = confirmation_delay
        self.max_entry_drift_r = max_entry_drift_r
        self.close_lookup_attempts = close_lookup_attempts
        self.max_open_positions = max_open_positions
        self.hedging_mode = hedging_mode
        self.management = management or ManagementConfig()
        # The lab measures R, not money: trade the market minimum instead of skipping.
        self.use_minimum_size = use_minimum_size
        self._close_lookup_misses: dict[str, int] = {}
        self._transaction_lookup_misses: dict[str, int] = {}
        self._notified_lock_r: dict[str, float] = {}
        self._last_fee_check: datetime | None = None
        self._forced_exit: dict[str, str] = {}   # deal ID -> reason for closes the bot initiated

    def execute(
        self,
        setup: MarketSetup,
        risk: RiskDecision,
        *,
        epic: str,
        max_risk_budget: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> DemoExecutionResult:
        if epic != self.allowed_epic:
            return self._record(
                "BLOCKED", f"Epic {epic!r} is not allowlisted", epic, strategy=setup.strategy
            )
        if not setup.ready or setup.direction is None or not risk.approved:
            return self._record(
                "BLOCKED", "Setup did not pass strategy and risk checks", epic, strategy=setup.strategy
            )
        if setup.entry is None or setup.stop is None or setup.target is None:
            return self._record(
                "BLOCKED", "Setup is missing broker protection levels", epic, strategy=setup.strategy
            )
        if any(item.direction != setup.direction for item in self.ledger.open_positions):
            return self._record(
                "BLOCKED",
                "This session holds positions in the opposite direction",
                epic,
                strategy=setup.strategy,
            )
        blocked = self._account_block_reason(setup.direction)
        if blocked:
            return self._record("BLOCKED", blocked, epic, strategy=setup.strategy)

        market = self.client.get_market_details(epic)
        snapshot = market.get("snapshot") or {}
        rules = market.get("dealingRules") or {}
        if snapshot.get("marketStatus") != "TRADEABLE":
            return self._record(
                "BLOCKED", f"{epic} is not currently tradeable", epic, strategy=setup.strategy
            )

        entry = self._entry_quote(snapshot, setup.direction)
        # The signal was priced at the candle close; the order fills at the live quote.
        # Shift the stop and target by the same drift so the planned R:R is preserved,
        # and refuse to chase a signal that has already moved a large part of 1R.
        drift = entry - setup.entry
        risk_distance = abs(setup.entry - setup.stop)
        if abs(drift) > self.max_entry_drift_r * risk_distance:
            return self._record(
                "BLOCKED",
                f"Price moved {drift:+.2f} since the signal, more than "
                f"{self.max_entry_drift_r:g}R ({self.max_entry_drift_r * risk_distance:.2f})",
                epic,
                strategy=setup.strategy,
            )
        decimals = int(snapshot.get("decimalPlacesFactor") or 2)
        stop = round(setup.stop + drift, decimals)
        target = round(setup.target + drift, decimals)
        reason = self._validate_protection(
            direction=setup.direction,
            entry=entry,
            stop=stop,
            target=target,
            rules=rules,
        )
        if reason:
            return self._record("BLOCKED", reason, epic, strategy=setup.strategy)

        size = self._normalized_size(risk.units, rules)
        if size is None:
            return self._record(
                "BLOCKED",
                "Risk-sized quantity is below Capital.com's minimum after applying the demo cap",
                epic,
                strategy=setup.strategy,
            )
        size, account_risk_block = self._apply_account_risk_budget(
            size,
            entry=entry,
            stop=stop,
            rules=rules,
            max_risk_budget=max_risk_budget,
            epic=epic,
            strategy=setup.strategy,
        )
        if size is None:
            return self._record(
                "BLOCKED",
                account_risk_block or "The account risk budget is exhausted",
                epic,
                strategy=setup.strategy,
            )
        return self._open(
            replace(setup, stop=stop, target=target),
            size=size,
            quoted_entry=entry,
            expected_reward_to_risk=risk.expected_reward_to_risk,
            role="signal",
            context=context,
        )

    def manage(
        self, *, allow_add: bool, max_risk_budget: float | None = None
    ) -> list[dict[str, Any]]:
        """Trail stops, extend targets, and add to a winner for this session's positions."""
        positions = [item for item in self.ledger.open_positions if item.deal_id]
        if not positions:
            return []
        market = self.client.get_market_details(self.allowed_epic)
        snapshot = market.get("snapshot") or {}
        rules = market.get("dealingRules") or {}
        if snapshot.get("marketStatus") != "TRADEABLE":
            return []
        decimals = int(snapshot.get("decimalPlacesFactor") or 2)

        events: list[dict[str, Any]] = []
        for position in positions:
            event = self._adjust_levels(position, snapshot, rules, decimals)
            if event:
                events.append(event)

        if allow_add:
            event = self._pyramid(
                positions[-1], snapshot, rules, decimals, max_risk_budget=max_risk_budget
            )
            if event:
                events.append(event)
        return events

    def reconcile(self) -> list[dict[str, Any]]:
        """Close local mirrors of positions the broker no longer holds."""
        tracked = [item for item in self.ledger.open_positions if item.deal_id]
        if not tracked:
            return []
        fee_events = self.collect_financing(tracked)
        open_ids: set[str] = set()
        order_to_position: dict[str, str] = {}
        open_sizes: dict[str, float] = {}
        for item in self._epic_positions():
            broker_position = item.get("position") or {}
            deal_id = str(broker_position.get("dealId"))
            open_ids.add(deal_id)
            try:
                open_sizes[deal_id] = float(broker_position.get("size"))
            except (TypeError, ValueError):
                pass
            if broker_position.get("workingOrderId"):
                order_to_position[str(broker_position["workingOrderId"])] = deal_id

        events: list[dict[str, Any]] = []
        activities: list[dict[str, Any]] | None = None
        transactions: list[dict[str, Any]] | None = None
        snapshot: dict[str, Any] | None = None
        for position in tracked:
            assert position.deal_id is not None
            if position.deal_id in open_ids:
                self._close_lookup_misses.pop(position.local_id, None)
                self._transaction_lookup_misses.pop(position.local_id, None)
                remaining = open_sizes.get(position.deal_id)
                if remaining is not None and remaining < position.units - 1e-6:
                    if activities is None:
                        activities = self._position_activities()
                    if transactions is None:
                        try:
                            transactions = self.client.get_transactions(transaction_type="TRADE")
                        except CapitalAPIError:
                            transactions = []
                    event = self._record_partials(
                        position, position.deal_id, activities, transactions, remaining
                    )
                    if event:
                        events.append(event)
                continue
            if position.deal_id in order_to_position:
                self.ledger.set_deal_id(position.local_id, order_to_position[position.deal_id])
                continue

            if activities is None:
                activities = self._position_activities()
            position_id = self._position_id_for(position.deal_id, activities)
            if position_id is None:
                # The opening is not in the broker history yet, so its state is unknown.
                continue
            if position_id != position.deal_id:
                self.ledger.set_deal_id(position.local_id, position_id)
                if position_id in open_ids:
                    continue

            closings = self._new_closings(position, position_id, activities)
            broker_result: tuple[float, str] | None = None
            if closings:
                self._close_lookup_misses.pop(position.local_id, None)
                exit_price, exit_reason, closing = self._combined_exit(position, position_id, closings)
                if exit_reason == "STOP" and stop_protects_entry(position):
                    exit_reason = "TRAILING_STOP"
                timestamp = self._activity_time(closing)
                if transactions is None:
                    try:
                        transactions = self.client.get_transactions(transaction_type="TRADE")
                    except CapitalAPIError:
                        transactions = []
                broker_result = self._broker_result(position_id, transactions, position.partials)
                if broker_result is None:
                    misses = self._transaction_lookup_misses.get(position.local_id, 0) + 1
                    self._transaction_lookup_misses[position.local_id] = misses
                    if misses < self.close_lookup_attempts:
                        continue
            else:
                misses = self._close_lookup_misses.get(position.local_id, 0) + 1
                self._close_lookup_misses[position.local_id] = misses
                if misses < self.close_lookup_attempts:
                    continue
                # The broker closed the position but its history has no exit yet; assume
                # whichever protection level is nearer the current market.
                if snapshot is None:
                    snapshot = (
                        self.client.get_market_details(self.allowed_epic).get("snapshot") or {}
                    )
                mid = (float(snapshot["bid"]) + float(snapshot["offer"])) / 2
                stop_nearer = abs(mid - position.stop) <= abs(mid - position.target)
                exit_price = position.stop if stop_nearer else position.target
                exit_reason = "STOP_ASSUMED" if stop_nearer else "TARGET_ASSUMED"
                timestamp = datetime.now(timezone.utc).isoformat()

            self._close_lookup_misses.pop(position.local_id, None)
            self._transaction_lookup_misses.pop(position.local_id, None)
            self._notified_lock_r.pop(position.local_id, None)
            extra: dict[str, Any] = {"source": "broker", "deal_id": position_id}
            if position.fees:
                extra["financing"] = round(position.fees, 2)
            if position.partials.get("units"):
                extra["partial_fills"] = len(position.partials.get("activities", [])) + 1
            if broker_result is not None:
                extra.update(
                    broker_pnl=broker_result[0],
                    broker_currency=broker_result[1],
                )
            events.append(
                self.ledger.close_position(
                    position.local_id,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    timestamp=timestamp,
                    extra=extra,
                )
            )
        return fee_events + events

    def collect_financing(
        self, tracked: list[PaperPosition], now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Allocate new broker overnight fees (SWAP) to this session's open positions.

        Capital.com books one fee per market and account at the daily break, without a position
        ID, and nothing trades during the break. So at the first check afterwards the open
        positions are the ones that paid it: this session takes the share of its units among all
        broker positions on the market that existed when the fee was booked.
        """
        now = now or datetime.now(timezone.utc)
        if self._last_fee_check and now - self._last_fee_check < timedelta(minutes=15):
            return []
        self._last_fee_check = now
        try:
            fees = self.client.get_transactions(transaction_type="SWAP")
        except CapitalAPIError:
            return []
        seen = set(self.ledger.state.fee_references)
        fresh = [
            item for item in fees
            if item.get("transactionType") in (None, "SWAP")
            and str(item.get("instrumentName") or "").upper() == self.allowed_epic.upper()
            and item.get("reference") is not None
            and str(item["reference"]) not in seen
        ]
        if not fresh:
            return []
        broker = [item.get("position") or {} for item in self._epic_positions()]
        events = []
        for fee in fresh:
            booked = self._utc(fee.get("dateUtc") or fee.get("dateUTC"))
            try:
                amount = float(fee["size"])
            except (KeyError, TypeError, ValueError):
                continue
            mine = [p for p in tracked if booked is None or self._utc(p.opened_at) < booked]
            total = sum(
                float(item.get("size") or 0)
                for item in broker
                if booked is None
                or self._utc(item.get("createdDateUTC")) is None
                or self._utc(item.get("createdDateUTC")) < booked
            )
            own = sum(p.units for p in mine)
            share = min(1.0, own / total) if total > 0 and own > 0 else 0.0
            allocated = amount * share
            for position in mine:
                position.fees += allocated * position.units / own if own else 0.0
            events.append(self.ledger.record_financing({
                "timestamp": fee.get("dateUtc") or fee.get("dateUTC"),
                "reference": str(fee["reference"]),
                "instrument": self.allowed_epic,
                "amount_total": amount,
                "share": round(share, 4),
                "amount": round(allocated, 4),
                "currency": str(fee.get("currency") or "").upper(),
                "positions": [p.local_id for p in mine] if share else [],
            }))
        return events

    @staticmethod
    def _utc(raw: Any) -> datetime | None:
        if not raw:
            return None
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def _adjust_levels(
        self,
        position: PaperPosition,
        snapshot: dict[str, Any],
        rules: dict[str, Any],
        decimals: int,
    ) -> dict[str, Any] | None:
        cfg = self.management
        risk_points = position.risk_points
        sign = 1.0 if position.direction == "BUY" else -1.0
        # Positions close at the opposite side of the spread.
        price = float(snapshot["bid"] if position.direction == "BUY" else snapshot["offer"])
        profit_r = position.profit_r(price)
        plan = plan_levels(position, price, cfg, decimals=decimals)
        if plan is None:
            return None
        stop, target, reasons = plan.stop, plan.target, list(plan.reasons)

        minimum = self._distance(rules.get("minStopOrProfitDistance"), price)
        if (price - stop) * sign < minimum:
            stop = position.stop
            reasons = [reason for reason in reasons if reason == "TARGET_EXTENDED"]
        if (target - price) * sign < minimum:
            target = position.target
            reasons = [reason for reason in reasons if reason != "TARGET_EXTENDED"]
        if not reasons:
            return None

        assert position.deal_id is not None
        deal_reference = self.client.update_position(
            position.deal_id, stop_level=stop, profit_level=target
        )
        confirmation = self._confirm(deal_reference)
        if confirmation.get("dealStatus") != "ACCEPTED":
            self._journal(
                {
                    "event": "DEMO_UPDATE_REJECTED",
                    "deal_id": position.deal_id,
                    "reason": confirmation.get("reason"),
                    "stop": stop,
                    "target": target,
                }
            )
            return None

        event = self.ledger.update_levels(
            position.local_id,
            stop=stop,
            target=target,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reason="+".join(reasons),
        )
        locked_r = (stop - position.entry) * sign / risk_points
        last_notified = self._notified_lock_r.get(position.local_id)
        notify = (
            "BREAKEVEN" in reasons
            or "TARGET_EXTENDED" in reasons
            or last_notified is None
            or locked_r - last_notified >= cfg.notify_step_r
        )
        if notify:
            self._notified_lock_r[position.local_id] = locked_r
        event.update({"price": price, "profit_r": profit_r, "locked_r": locked_r, "notify": notify})
        return event

    def _pyramid(
        self,
        newest: PaperPosition,
        snapshot: dict[str, Any],
        rules: dict[str, Any],
        decimals: int,
        *,
        max_risk_budget: float | None = None,
    ) -> dict[str, Any] | None:
        exit_price = float(snapshot["bid"] if newest.direction == "BUY" else snapshot["offer"])
        if not pyramid_ready(newest, exit_price, self.management):
            return None
        if self._account_block_reason(newest.direction):
            return None

        entry = self._entry_quote(snapshot, newest.direction)
        stop = round(newest.stop, decimals)
        target = round(newest.target, decimals)
        if self._validate_protection(
            direction=newest.direction, entry=entry, stop=stop, target=target, rules=rules
        ):
            return None
        size = self._normalized_size(newest.units, rules)
        if size is None:
            return None
        size, _ = self._apply_account_risk_budget(
            size,
            entry=entry,
            stop=stop,
            rules=rules,
            max_risk_budget=max_risk_budget,
            epic=self.allowed_epic,
            strategy=newest.strategy,
        )
        if size is None:
            return None
        setup = MarketSetup(
            decision="READY_LONG" if newest.direction == "BUY" else "READY_SHORT",
            timestamp=datetime.now(timezone.utc),
            reason="Added to a winning position.",
            bias="BULLISH" if newest.direction == "BUY" else "BEARISH",
            strategy=newest.strategy,
            entry=entry,
            stop=stop,
            target=target,
        )
        result = self._open(
            setup,
            size=size,
            quoted_entry=entry,
            expected_reward_to_risk=abs(target - entry) / abs(entry - stop),
            role="add",
        )
        return result.paper_event if result.status == "OPENED" else None

    def _open(
        self,
        setup: MarketSetup,
        *,
        size: float,
        quoted_entry: float,
        expected_reward_to_risk: float,
        role: Literal["signal", "add"],
        context: dict[str, Any] | None = None,
    ) -> DemoExecutionResult:
        assert setup.direction is not None and setup.stop is not None and setup.target is not None
        epic = self.allowed_epic
        deal_reference = self.client.create_position(
            epic=epic,
            direction=setup.direction,
            size=size,
            stop_level=setup.stop,
            profit_level=setup.target,
        )
        confirmation = self._confirm(deal_reference)
        if confirmation.get("dealStatus") != "ACCEPTED":
            reason = str(confirmation.get("reason") or "Capital.com rejected the demo order")
            return self._record(
                "REJECTED",
                reason,
                epic,
                strategy=setup.strategy,
                size=size,
                deal_reference=deal_reference,
            )

        deal_id = self._deal_id(confirmation)
        confirmed_entry = float(confirmation.get("level") or quoted_entry)
        mirrored_setup = replace(setup, entry=confirmed_entry)
        mirrored_risk = RiskDecision(
            approved=True,
            reason="Capital.com demo order accepted.",
            units=size,
            risk_budget=abs(confirmed_entry - setup.stop) * size,
            expected_reward_to_risk=expected_reward_to_risk,
        )
        paper_event = self.ledger.open_position(
            mirrored_setup, mirrored_risk, deal_id=deal_id, role=role, context=context
        )
        return self._record(
            "OPENED",
            "Capital.com accepted the protected demo position"
            + (" (added to a winner)" if role == "add" else ""),
            epic,
            strategy=setup.strategy,
            size=size,
            deal_reference=deal_reference,
            deal_id=deal_id,
            paper_event=paper_event,
        )

    def _confirm(self, deal_reference: str) -> dict[str, Any]:
        for attempt in range(self.confirmation_attempts):
            try:
                return self.client.get_confirmation(deal_reference)
            except CapitalAPIError:
                if attempt + 1 == self.confirmation_attempts:
                    raise
                time.sleep(self.confirmation_delay)
        raise AssertionError("unreachable")

    def _account_block_reason(self, direction: str) -> str | None:
        # Other sessions may hold positions on the same account.
        positions = self._epic_positions()
        if len(positions) >= self.max_open_positions:
            return (
                f"The demo account already has {len(positions)} open position(s), "
                f"the cap is {self.max_open_positions}"
            )
        if not self.hedging_mode and any(
            (item.get("position") or {}).get("direction") not in (None, direction)
            for item in positions
        ):
            return "Hedging mode is off, so an opposite position would close another session's trade"
        return None

    def close_all(self, reason: str) -> list[dict[str, Any]]:
        """Close this session's broker positions at market (e.g. before the weekend).

        The closes are recorded by `reconcile()` with the broker's real fill, labelled `reason`.
        """
        tracked = [item for item in self.ledger.open_positions if item.deal_id]
        if not tracked:
            return []
        broker_ids: dict[str, str] = {}
        for item in self._epic_positions():
            broker_position = item.get("position") or {}
            if broker_position.get("dealId"):
                deal_id = str(broker_position["dealId"])
                broker_ids[deal_id] = deal_id
                if broker_position.get("workingOrderId"):
                    broker_ids[str(broker_position["workingOrderId"])] = deal_id
        events = []
        for position in tracked:
            broker_id = broker_ids.get(str(position.deal_id))
            if broker_id is None:
                continue  # already closed at the broker; reconcile records it
            reference = self.client.close_position(broker_id)
            status = self._confirm(reference).get("dealStatus")
            self._forced_exit[broker_id] = reason
            event = {"event": "FORCED_CLOSE", "reason": reason, "deal_id": broker_id,
                     "status": status, "local_id": position.local_id}
            self._journal(event)
            events.append(event)
        return events

    def _epic_positions(self) -> list[dict[str, Any]]:
        return [
            item
            for item in self.client.get_positions()
            if (item.get("market") or {}).get("epic", self.allowed_epic) == self.allowed_epic
        ]

    def _position_activities(self) -> list[dict[str, Any]]:
        return [item for item in self.client.get_activity() if item.get("type") == "POSITION"]

    @staticmethod
    def _position_id_for(deal_id: str, activities: list[dict[str, Any]]) -> str | None:
        """Map an order ID to its position ID; position IDs map to themselves."""
        for item in activities:
            if item.get("dealId") == deal_id:
                return deal_id
        for item in activities:
            if (item.get("details") or {}).get("workingOrderId") == deal_id and item.get("dealId"):
                return str(item["dealId"])
        return None

    @staticmethod
    def _activity_key(activity: dict[str, Any]) -> str:
        details = activity.get("details") or {}
        return f"{activity.get('dateUTC')}|{details.get('size')}|{details.get('level')}"

    def _new_closings(
        self, position: PaperPosition, position_id: str, activities: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Closing fills of this position not yet counted as partials, oldest first."""
        counted = set(position.partials.get("activities", []))
        found = [
            item for item in activities
            if item.get("dealId") == position_id
            and "openPrice" in (item.get("details") or {})
            and (item.get("details") or {}).get("level") is not None
            and self._activity_key(item) not in counted
        ]
        return sorted(found, key=lambda item: str(item.get("dateUTC")))

    def _exit_reason(self, position_id: str, deal_id: str | None, activity: dict[str, Any]) -> str:
        source = str(activity.get("source") or "UNKNOWN")
        reason = {"SL": "STOP", "TP": "TARGET", "USER": "MANUAL"}.get(source, source)
        if reason == "MANUAL":
            reason = self._forced_exit.pop(position_id, self._forced_exit.pop(str(deal_id), "MANUAL"))
        return reason

    def _combined_exit(
        self, position: PaperPosition, position_id: str, closings: list[dict[str, Any]]
    ) -> tuple[float, str, dict[str, Any]]:
        """Size-weighted exit of earlier partial fills plus the final ones, and the exit reason."""
        partials = position.partials
        units = float(partials.get("units", 0.0))
        value = float(partials.get("value", 0.0))
        reasons = list(partials.get("reasons", []))
        for item in closings:
            details = item.get("details") or {}
            size = float(details.get("size") or 0.0)
            units += size
            value += float(details["level"]) * size
            reason = self._exit_reason(position_id, position.deal_id, item)
            if reason not in reasons:
                reasons.append(reason)
        exit_price = value / units if units > 0 else float((closings[-1].get("details") or {})["level"])
        return exit_price, "+".join(reasons), closings[-1]

    def _record_partials(
        self,
        position: PaperPosition,
        position_id: str,
        activities: list[dict[str, Any]],
        transactions: list[dict[str, Any]],
        remaining: float,
    ) -> dict[str, Any] | None:
        """Store broker fills that closed part of a still-open position, once each."""
        closings = self._new_closings(position, position_id, activities)
        if not closings:
            return None
        partials = position.partials
        partials.setdefault("units", 0.0)
        partials.setdefault("value", 0.0)
        partials.setdefault("reasons", [])
        partials.setdefault("activities", [])
        partials.setdefault("broker_pnl", 0.0)
        partials.setdefault("transactions", [])
        for item in closings:
            details = item.get("details") or {}
            size = float(details.get("size") or 0.0)
            partials["units"] += size
            partials["value"] += float(details["level"]) * size
            reason = self._exit_reason(position_id, position.deal_id, item)
            if reason not in partials["reasons"]:
                partials["reasons"].append(reason)
            partials["activities"].append(self._activity_key(item))
        for item in self._trade_transactions(position_id, transactions, partials):
            partials["broker_pnl"] += float(item["size"])
            partials["currency"] = str(item.get("currency") or "").strip().upper()
            partials["transactions"].append(str(item.get("reference")))
        last = closings[-1]
        return self.ledger.record_partial_close(position.local_id, {
            "timestamp": self._activity_time(last),
            "deal_id": position_id,
            "closed_units": sum(float((c.get("details") or {}).get("size") or 0) for c in closings),
            "exit_price": float((last.get("details") or {})["level"]),
            "remaining_units": remaining,
            "reasons": list(partials["reasons"]),
            "partial_broker_pnl": partials["broker_pnl"],
        })

    @staticmethod
    def _trade_transactions(
        position_id: str, transactions: list[dict[str, Any]], partials: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        counted = set((partials or {}).get("transactions", []))
        found = []
        for item in transactions:
            if str(item.get("dealId")) != position_id:
                continue
            if item.get("transactionType") not in (None, "TRADE"):
                continue
            if item.get("status") not in (None, "PROCESSED"):
                continue
            if item.get("reference") is not None and str(item["reference"]) in counted:
                continue
            try:
                float(item["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if str(item.get("currency") or "").strip():
                found.append(item)
        return found

    @classmethod
    def _broker_result(
        cls,
        position_id: str,
        transactions: list[dict[str, Any]],
        partials: dict[str, Any] | None = None,
    ) -> tuple[float, str] | None:
        """The signed account-currency result of a closed broker position, partial fills included."""
        found = cls._trade_transactions(position_id, transactions, partials)
        if not found:
            return None
        amount = sum(float(item["size"]) for item in found) + float((partials or {}).get("broker_pnl", 0.0))
        currency = str(found[-1].get("currency")).strip().upper()
        return amount, currency

    @staticmethod
    def _activity_time(activity: dict[str, Any]) -> str:
        raw = activity.get("dateUTC")
        if not raw:
            return datetime.now(timezone.utc).isoformat()
        return datetime.fromisoformat(str(raw)).replace(tzinfo=timezone.utc).isoformat()

    def _normalized_size(
        self,
        requested: float,
        rules: dict[str, Any],
        *,
        allow_minimum: bool | None = None,
    ) -> float | None:
        minimum = self._rule_value(rules, "minDealSize", 0.0)
        maximum = self._rule_value(rules, "maxDealSize", self.max_size)
        increment = self._rule_value(rules, "minSizeIncrement", minimum or 0.1)
        capped = min(requested, self.max_size, maximum)
        use_minimum = self.use_minimum_size if allow_minimum is None else allow_minimum
        if capped < minimum and use_minimum and minimum <= maximum:
            capped = minimum
        if capped < minimum or increment <= 0:
            return None
        steps = math.floor((Decimal(str(capped)) - Decimal(str(minimum))) / Decimal(str(increment)))
        normalized = Decimal(str(minimum)) + Decimal(steps) * Decimal(str(increment))
        return float(normalized)

    def _apply_account_risk_budget(
        self,
        requested_size: float,
        *,
        entry: float,
        stop: float,
        rules: dict[str, Any],
        max_risk_budget: float | None,
        epic: str,
        strategy: str,
    ) -> tuple[float | None, str | None]:
        if max_risk_budget is None:
            return requested_size, None
        remaining = max(0.0, float(max_risk_budget))
        unit_risk = abs(entry - stop) * self.ledger.point_value_per_unit
        requested_risk = requested_size * unit_risk
        if requested_risk <= remaining + 1e-9:
            return requested_size, None

        max_units = remaining / unit_risk if unit_risk > 0 else 0.0
        scaled = self._normalized_size(max_units, rules, allow_minimum=False)
        common = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "epic": epic,
            "strategy": strategy,
            "requested_size": requested_size,
            "requested_risk": requested_risk,
            "remaining_risk_budget": remaining,
            "unit_risk": unit_risk,
        }
        if scaled is None:
            reason = "Remaining account risk budget cannot fit Capital.com's minimum size"
            self._record_account_risk_event(
                {"event": "ACCOUNT_RISK_BLOCK", "reason": reason, **common}
            )
            return None, reason

        self._record_account_risk_event(
            {
                "event": "ACCOUNT_RISK_SCALED",
                "size": scaled,
                "scaled_risk": scaled * unit_risk,
                **common,
            }
        )
        return scaled, None

    def _record_account_risk_event(self, payload: dict[str, Any]) -> None:
        self.ledger.record_event(payload)
        self._journal(payload)

    @staticmethod
    def _entry_quote(snapshot: dict[str, Any], direction: str) -> float:
        key = "offer" if direction == "BUY" else "bid"
        value = snapshot.get(key)
        if value is None:
            raise ValueError(f"Capital.com market snapshot is missing {key}")
        return float(value)

    def _validate_protection(
        self,
        *,
        direction: str,
        entry: float,
        stop: float,
        target: float,
        rules: dict[str, Any],
    ) -> str | None:
        minimum = self._distance(rules.get("minStopOrProfitDistance"), entry)
        maximum = self._distance(rules.get("maxStopOrProfitDistance"), entry)
        stop_distance = entry - stop if direction == "BUY" else stop - entry
        target_distance = target - entry if direction == "BUY" else entry - target
        if stop_distance <= 0 or target_distance <= 0:
            return "The market moved beyond the proposed stop or target before submission"
        if stop_distance < minimum or target_distance < minimum:
            return "The proposed stop or target is inside Capital.com's minimum distance"
        if maximum and (stop_distance > maximum or target_distance > maximum):
            return "The proposed stop or target exceeds Capital.com's maximum distance"
        return None

    @staticmethod
    def _distance(rule: Any, entry: float) -> float:
        if not isinstance(rule, dict):
            return 0.0
        value = float(rule.get("value") or 0.0)
        return entry * value / 100.0 if rule.get("unit") == "PERCENTAGE" else value

    @staticmethod
    def _rule_value(rules: dict[str, Any], name: str, default: float) -> float:
        rule = rules.get(name)
        return float(rule.get("value", default)) if isinstance(rule, dict) else default

    @staticmethod
    def _opened_position_id(confirmation: dict[str, Any]) -> str | None:
        for affected in confirmation.get("affectedDeals") or []:
            if affected.get("status") == "OPENED" and affected.get("dealId"):
                return str(affected["dealId"])
        return None

    @classmethod
    def _deal_id(cls, confirmation: dict[str, Any]) -> str | None:
        # Prefer the opened position's ID; the top-level dealId is the order's ID.
        opened = cls._opened_position_id(confirmation)
        if opened:
            return opened
        if confirmation.get("dealId"):
            return str(confirmation["dealId"])
        return None

    def _journal(self, payload: dict[str, Any]) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")

    def _record(
        self,
        status: ExecutionStatus,
        reason: str,
        epic: str,
        *,
        strategy: str | None = None,
        size: float | None = None,
        deal_reference: str | None = None,
        deal_id: str | None = None,
        paper_event: dict[str, Any] | None = None,
    ) -> DemoExecutionResult:
        result = DemoExecutionResult(
            status=status,
            reason=reason,
            epic=epic,
            strategy=strategy,
            size=size,
            deal_reference=deal_reference,
            deal_id=deal_id,
            paper_event=paper_event,
        )
        self._journal({"event": "DEMO_EXECUTION", **result.to_dict()})
        return result
