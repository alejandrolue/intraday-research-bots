from __future__ import annotations

from dataclasses import asdict, dataclass

from .models import MarketSetup


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.0025
    max_daily_loss: float = 0.01
    max_trades_per_day: int = 3
    max_spread: float = 0.80
    minimum_reward_to_risk: float = 1.5
    point_value_per_unit: float = 1.0
    max_positions: int = 1
    cap_daily_risk: bool = False


@dataclass(frozen=True)
class RiskState:
    balance: float
    daily_realized_pnl: float
    trades_today: int
    open_positions: int = 0
    open_direction: str | None = None
    open_risk: float = 0.0


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    units: float = 0.0
    risk_budget: float = 0.0
    expected_reward_to_risk: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class RiskEngine:
    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()

    def entry_block_reason(self, state: RiskState, direction: str | None) -> str | None:
        """Account-level reasons that forbid any new position, including adds."""
        cfg = self.config
        if state.open_positions >= cfg.max_positions:
            return f"{state.open_positions} position(s) already open; the cap is {cfg.max_positions}."
        if state.open_direction is not None and direction != state.open_direction:
            return f"Open positions are {state.open_direction}; opposite entries are blocked."
        if state.trades_today >= cfg.max_trades_per_day:
            return "The daily paper-trade limit has been reached."
        if state.daily_realized_pnl <= -(state.balance * cfg.max_daily_loss):
            return "The daily paper-loss limit has been reached."
        if cfg.cap_daily_risk and self._remaining_daily_risk(state) <= 0:
            return "Realized loss plus open stop-risk has reached the daily risk limit."
        return None

    def _remaining_daily_risk(self, state: RiskState) -> float:
        cfg = self.config
        daily_limit = state.balance * cfg.max_daily_loss
        return daily_limit + state.daily_realized_pnl - state.open_risk

    def evaluate(
        self,
        setup: MarketSetup,
        *,
        spread: float,
        state: RiskState,
    ) -> RiskDecision:
        cfg = self.config
        if not setup.ready or setup.entry is None or setup.stop is None or setup.target is None:
            return RiskDecision(False, "The strategy has not produced an executable candidate.")
        blocked = self.entry_block_reason(state, setup.direction)
        if blocked:
            return RiskDecision(False, blocked)
        if spread > cfg.max_spread:
            return RiskDecision(False, f"Spread {spread:.4f} exceeds the configured maximum.")

        risk_distance = abs(setup.entry - setup.stop)
        reward_distance = abs(setup.target - setup.entry)
        if risk_distance <= 0:
            return RiskDecision(False, "Stop distance is not positive.")
        reward_to_risk = reward_distance / risk_distance
        if reward_to_risk < cfg.minimum_reward_to_risk:
            return RiskDecision(False, "Reward-to-risk is below the configured minimum.")

        risk_budget = state.balance * cfg.risk_per_trade
        scaled = False
        if cfg.cap_daily_risk:
            remaining = self._remaining_daily_risk(state)
            if remaining <= 0:
                return RiskDecision(
                    False, "Realized loss plus open stop-risk has reached the daily risk limit."
                )
            if remaining < risk_budget:
                risk_budget = remaining
                scaled = True
        unit_risk = risk_distance * cfg.point_value_per_unit
        if unit_risk <= 0:
            return RiskDecision(False, "Point-value configuration is invalid.")
        units = risk_budget / unit_risk
        if units <= 0:
            return RiskDecision(False, "Calculated paper position size is not positive.")

        return RiskDecision(
            True,
            (
                "Candidate passed; size was reduced to the remaining daily risk budget."
                if scaled
                else "Candidate passed all deterministic paper-risk checks."
            ),
            units=units,
            risk_budget=risk_budget,
            expected_reward_to_risk=reward_to_risk,
        )
