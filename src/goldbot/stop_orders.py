"""Issue #101: enter range breakouts with broker-side stop orders instead of market orders.

Once a day, while the strategy's window is open and the range has not broken yet, two STOP entry orders
are placed: a buy one spread above the range high and a sell one spread below the low, each protected by
a stop at the other side of the range and valid until the window ends. When one fills, the other is
cancelled and the position is added to the session's ledger with the broker's real fill, so the existing
reconciliation, daily close and weekend close handle it like any other position. If both fill in one fast
move, the second is closed at once. Every fill records its slippage against the order level.

State lives in `bracket.json` in the session folder, so a restart neither re-places nor forgets orders.

If the spread is too wide when the orders would be placed, nothing is placed and the day is not used up: the
next poll tries again with the latest candle, for as long as the window is open and the range has not broken
(amendment on #101, 16 Oct 2026, after a 1.95 spread at the window start skipped a whole day).

If the live price is already through one side's level when the orders would be placed, the broker refuses that
stop order (a buy stop must be above the price). That side is then entered at market with the same stop and
target, like the backtest's fill at the level, and no other order is placed. More than 0.5R past the level, the
day is skipped (issue #196, after gold-london lost 4 Nov 2026 this way).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .capital import CapitalAPIError
from .models import MarketSetup
from .risk import RiskDecision
from .range_breakout import FAR_TARGET_R

MAX_MARKET_DRIFT_R = 0.5   # the same limit as the entry-drift rule for market entries (execution.py)


def _spread_too_wide(reason: str) -> bool:
    return reason.startswith("Spread ") and "exceeds the configured maximum" in reason


class StopOrderBracket:
    def __init__(self, client, executor, ledger, risk_engine, state_path: Path, *, epic: str, notifier=None) -> None:
        self.client = client
        self.executor = executor
        self.ledger = ledger
        self.risk_engine = risk_engine
        self.state_path = state_path
        self.epic = epic
        self.notifier = notifier
        self.state: dict[str, Any] = self._load()

    # ------------------------------------------------------------------ public
    def sync(self, strategy, get_candles, now: datetime, *, blocked: str | None = None,
             max_risk_budget: float | None = None) -> list[dict[str, Any]]:
        """Run once per poll. `blocked` (weekend or daily close, pause) stops new orders and cancels pending
        ones. `get_candles()` is only called when today's orders could still be placed."""
        events: list[dict[str, Any]] = []
        orders = self.state.get("orders") or {}
        if orders and not self.state.get("filled"):
            events += self._check_fills(now)
        orders = self.state.get("orders") or {}
        pending = {side: o for side, o in orders.items() if o.get("status") == "PENDING"}
        expired = self.state.get("expires") and now >= datetime.fromisoformat(self.state["expires"])
        if pending and (blocked or expired):
            events += self._cancel(pending, "EXPIRED" if expired else "BLOCKED")
        if blocked or self.ledger.open_positions:
            return events
        if self.state.get("day") == now.astimezone(strategy.zone).date().isoformat():
            return events  # today's orders were already placed (or skipped)
        local = now.astimezone(strategy.zone)
        if local.weekday() >= 5 or not strategy.window_start <= (local.hour, local.minute) < strategy.window_end:
            return events
        candles = get_candles()
        plan = strategy.bracket(candles, now)
        if plan and self.state.get("day") != plan["day"]:
            events += self._place(plan, candles[-1], now, max_risk_budget)
        return events

    # ----------------------------------------------------------------- placing
    def _place(self, plan: dict[str, Any], bar, now: datetime,
               max_risk_budget: float | None = None) -> list[dict[str, Any]]:
        self.state = {"day": plan["day"], "plan": plan, "expires": plan["expires"], "orders": {}, "filled": None,
                      "spread_wait_day": self.state.get("spread_wait_day")}
        self._save()
        market = self.client.get_market_details(self.epic)
        rules = market.get("dealingRules") or {}
        decimals = int((market.get("snapshot") or {}).get("decimalPlacesFactor") or 2)
        events = []
        snapshot = market.get("snapshot") or {}
        through = [side for side in ("BUY", "SELL") if self._through(side, round(plan[side]["level"], decimals), snapshot)]
        for side in ("BUY", "SELL"):
            if through and side not in through:
                continue   # the other side is entered at market; its stop order is not needed
            level, stop = round(plan[side]["level"], decimals), round(plan[side]["stop"], decimals)
            sign = 1 if side == "BUY" else -1
            target = round(level + sign * FAR_TARGET_R * abs(level - stop), decimals)
            setup = MarketSetup("READY_LONG" if side == "BUY" else "READY_SHORT", bar.timestamp, "Stop order",
                                "BULLISH" if side == "BUY" else "BEARISH", "bracket", entry=level, stop=stop, target=target)
            risk = self.risk_engine.evaluate(setup, spread=plan["spread"], state=self.ledger.risk_state(now))
            if not risk.approved and _spread_too_wide(risk.reason):
                return self._wait_for_spread(plan, risk.reason)
            units = risk.units
            if risk.approved and max_risk_budget is not None and level != stop:
                units = min(units, max_risk_budget / abs(level - stop))   # day-trading portfolio budget (#166)
            size = self.executor._normalized_size(units, rules) if risk.approved else None
            if size is None:
                events.append(self._event("BRACKET_SKIPPED", side=side, reason=risk.reason if not risk.approved
                                          else "Size below the broker minimum"))
                continue
            if side in through:
                events += self._market_entry(side, level, stop, target, size, snapshot, now, rules)
                continue
            try:
                reference = self.client.create_working_order(
                    epic=self.epic, direction=side, size=size, level=level, stop_level=stop,
                    profit_level=target, good_till=datetime.fromisoformat(plan["expires"]))
                confirmation = self.executor._confirm(reference)
            except CapitalAPIError as exc:
                events += self._after_rejection(side, level, stop, target, size, now, rules, str(exc))
                if self._entered_at_market(side):
                    break
                continue
            if confirmation.get("dealStatus") != "ACCEPTED":
                events += self._after_rejection(side, level, stop, target, size, now, rules,
                                                str(confirmation.get("reason")))
                if self._entered_at_market(side):
                    break
                continue
            deal_id = str(confirmation.get("dealId") or "")
            self.state["orders"][side] = {"deal_id": deal_id, "level": level, "stop": stop, "target": target,
                                          "size": size, "status": "PENDING"}
            self._save()
            events.append(self._event("BRACKET_PLACED", side=side, deal_id=deal_id, level=level, stop=stop, size=size))
        return events

    @staticmethod
    def _through(side: str, level: float, snapshot: dict[str, Any]) -> bool:
        """True when the live quote is already at or past the order level, so the broker would refuse the stop."""
        quote = snapshot.get("offer" if side == "BUY" else "bid")
        if quote is None:
            return False
        return float(quote) >= level if side == "BUY" else float(quote) <= level

    def _after_rejection(self, side: str, level: float, stop: float, target: float, size: float,
                         now: datetime, rules: dict[str, Any], reason: str) -> list[dict[str, Any]]:
        """A refused stop order often means the price crossed the level after the snapshot was read (6 Nov 2026,
        jobs report, #203). Re-read the quote once; if it is now through the level, enter at market as in #196
        and cancel the other side's pending stop order."""
        events = [self._event("BRACKET_REJECTED", side=side, reason=reason)]
        try:
            snapshot = self.client.get_market_details(self.epic).get("snapshot") or {}
        except CapitalAPIError:
            return events
        if not self._through(side, level, snapshot):
            return events
        pending = {s: o for s, o in (self.state.get("orders") or {}).items() if o.get("status") == "PENDING"}
        events += self._market_entry(side, level, stop, target, size, snapshot, now, rules)
        if self._entered_at_market(side) and pending:
            events += self._cancel(pending, "OTHER_SIDE_ENTERED")
        return events

    def _entered_at_market(self, side: str) -> bool:
        return bool((self.state.get("orders") or {}).get(side, {}).get("market"))

    def _market_entry(self, side: str, level: float, stop: float, target: float, size: float,
                      snapshot: dict[str, Any], now: datetime, rules: dict[str, Any]) -> list[dict[str, Any]]:
        """Enter at market the side whose level the price has already passed (issue #196)."""
        quote = float(snapshot.get("offer" if side == "BUY" else "bid"))
        drift_r = abs(quote - level) / abs(level - stop) if level != stop else float("inf")
        if drift_r > MAX_MARKET_DRIFT_R:
            return [self._event("BRACKET_SKIPPED", side=side, quote=quote, level=level, drift_r=round(drift_r, 4),
                                reason=f"Price already {drift_r:.2f}R past the level (limit {MAX_MARKET_DRIFT_R}R)")]
        # Sized for the stop distance from the quote, so the risk stays the planned amount.
        size = self.executor._normalized_size(size * abs(level - stop) / abs(quote - stop), rules)
        if size is None:
            return [self._event("BRACKET_SKIPPED", side=side, reason="Size below the broker minimum")]
        try:
            reference = self.client.create_position(epic=self.epic, direction=side, size=size, stop_level=stop,
                                                    profit_level=target)
            confirmation = self.executor._confirm(reference)
        except CapitalAPIError as exc:
            return [self._event("BRACKET_MARKET_REJECTED", side=side, reason=str(exc))]
        if confirmation.get("dealStatus") != "ACCEPTED":
            return [self._event("BRACKET_MARKET_REJECTED", side=side, reason=str(confirmation.get("reason")))]
        deal_id = str(confirmation.get("dealId") or "")
        self.state["orders"][side] = {"deal_id": deal_id, "level": level, "stop": stop, "target": target,
                                      "size": size, "status": "MARKET", "market": True}
        self._save()
        events = [self._event("BRACKET_MARKET_ENTRY", side=side, deal_id=deal_id, level=level, quote=quote,
                              note="Price already through the level; entered at market")]
        position = {"dealId": deal_id, "level": confirmation.get("level") or quote, "size": size,
                    "stopLevel": stop, "profitLevel": target}
        events.append(self._register(side, position, now))
        return events

    def _wait_for_spread(self, plan: dict[str, Any], reason: str) -> list[dict[str, Any]]:
        """Leave the day open so the next poll retries; journal the first wait of the day only."""
        first = self.state.get("spread_wait_day") != plan["day"]
        self.state = {"spread_wait_day": plan["day"]}
        self._save()
        if not first:
            return []
        return [self._event("BRACKET_SPREAD_WAIT", day=plan["day"], reason=reason,
                            note="Spread too wide; retrying every poll while the window is open")]

    # ------------------------------------------------------------------- fills
    def _check_fills(self, now: datetime) -> list[dict[str, Any]]:
        orders = self.state["orders"]
        ids = {o["deal_id"]: side for side, o in orders.items() if o.get("status") == "PENDING"}
        if not ids:
            return []
        filled = []
        positions = [item.get("position") or {} for item in self.executor._epic_positions()]
        for position in positions:
            source = str(position.get("workingOrderId") or "")
            if source in ids:
                filled.append((ids[source], position))
        if not filled:
            try:
                live = {str((o.get("workingOrderData") or {}).get("dealId")) for o in self.client.get_working_orders()}
            except CapitalAPIError:
                return []
            gone = {deal_id: side for deal_id, side in ids.items() if deal_id not in live}
            if not gone:
                return []
            # Read the positions again: an order that filled between the first read and the order list is
            # missing from both (28 Oct 2026, issue #174).
            try:
                positions = [item.get("position") or {} for item in self.executor._epic_positions()]
                activities = self.client.get_activity()
            except CapitalAPIError:
                return []   # unsure: leave the orders pending and look again next poll
            tracked = {item.deal_id for item in self.ledger.open_positions}
            for deal_id, side in gone.items():
                match = [p for p in positions if str(p.get("workingOrderId") or "") == deal_id]
                # If the broker did not link the position to the order, match an untracked one of the same side
                # and size.
                match = match or [p for p in positions if str(p.get("dealId")) not in tracked
                                  and p.get("direction") == side
                                  and abs(float(p.get("size") or 0) - orders[side]["size"]) < 1e-9]
                if match:
                    filled.append((side, match[0]))
                    continue
                fill = self._fill_from_activity(deal_id, activities)
                if fill is not None:
                    # Filled and already closed (or not listed yet): register it, reconciliation closes it from
                    # the broker's real exit.
                    filled.append((side, fill))
                elif not self._executed(deal_id, activities):
                    orders[side]["status"] = "GONE"   # expired or cancelled at the broker
            self._save()
            if not filled:
                return []
        events = []
        first_side, first = filled[0]
        events.append(self._register(first_side, first, now))
        for side, position in filled[1:]:
            orders[side]["status"] = "FILLED_BOTH"
            deal_id = str(position.get("dealId"))
            try:
                self.client.close_position(deal_id)
            except CapitalAPIError as exc:
                events.append(self._event("BRACKET_BOTH_CLOSE_FAILED", side=side, deal_id=deal_id, reason=str(exc)))
                continue
            events.append(self._event("BRACKET_BOTH_FILLED", side=side, deal_id=deal_id,
                                      note="Both sides filled; the second position was closed at once"))
        others = {side: o for side, o in orders.items() if o.get("status") == "PENDING"}
        events += self._cancel(others, "OTHER_SIDE_FILLED")
        self._save()
        return events

    @staticmethod
    def _fill_from_activity(deal_id: str, activities: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The position the broker opened from working order `deal_id`, as listed in the activity log."""
        for item in activities:
            details = item.get("details") or {}
            if item.get("type") == "POSITION" and str(details.get("workingOrderId") or "") == deal_id:
                return {"dealId": item.get("dealId"), "level": details.get("level"), "size": details.get("size"),
                        "direction": details.get("direction")}
        return None

    @staticmethod
    def _executed(deal_id: str, activities: list[dict[str, Any]]) -> bool:
        return any(item.get("type") == "WORKING_ORDER" and str(item.get("dealId")) == deal_id
                   and item.get("status") == "EXECUTED" for item in activities)

    def _register(self, side: str, position: dict[str, Any], now: datetime) -> dict[str, Any]:
        order = self.state["orders"][side]
        sign = 1 if side == "BUY" else -1
        fill = float(position.get("level") or order["level"])
        size = float(position.get("size") or order["size"])
        stop = float(position.get("stopLevel") or order["stop"])
        target = float(position.get("profitLevel") or order["target"])
        deal_id = str(position.get("dealId"))
        setup = MarketSetup("READY_LONG" if side == "BUY" else "READY_SHORT", now, "Stop order filled",
                            "BULLISH" if side == "BUY" else "BEARISH", "bracket", entry=fill, stop=stop, target=target)
        risk = RiskDecision(True, "Filled stop order", units=size, risk_budget=abs(fill - stop) * size)
        paper_event = self.ledger.open_position(setup, risk, deal_id=deal_id)
        order["status"] = "FILLED"
        self.state["filled"] = side
        self._save()
        slippage = (fill - order["level"]) * sign
        if self.notifier is not None:
            try:
                self.notifier.trade_opened(paper_event, deal_id=deal_id)
            except Exception as exc:  # a Telegram failure must not stop trading
                self._event("WARNING", reason=f"Telegram open message failed: {exc}")
        return self._event("BRACKET_FILLED", side=side, deal_id=deal_id, order_level=order["level"], fill=fill,
                           slippage_points=round(slippage, 4),
                           slippage_r=round(slippage / abs(order["level"] - stop), 4) if order["level"] != stop else None)

    def _cancel(self, orders: dict[str, dict[str, Any]], reason: str) -> list[dict[str, Any]]:
        events = []
        for side, order in orders.items():
            try:
                self.client.delete_working_order(order["deal_id"])
                order["status"] = "CANCELLED"
                events.append(self._event("BRACKET_CANCELLED", side=side, deal_id=order["deal_id"], reason=reason))
            except CapitalAPIError as exc:
                # Already filled or expired at the broker; the next poll's fill check sorts it out.
                events.append(self._event("BRACKET_CANCEL_FAILED", side=side, deal_id=order["deal_id"], reason=str(exc)))
        self._save()
        return events

    # ------------------------------------------------------------------- state
    def _event(self, name: str, **fields: Any) -> dict[str, Any]:
        payload = {"event": name, "day": self.state.get("day"), **fields}
        self.ledger.record_event(payload)
        return payload

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, sort_keys=True))
        tmp.replace(self.state_path)
