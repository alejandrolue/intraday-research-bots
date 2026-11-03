"""Trade-management rules shared by live execution and the backtester.

All distances are multiples of a position's initial risk (R): the distance from its entry to
the stop it was opened with.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ManagementConfig:
    trail_start_r: float = 1.0
    trail_distance_r: float = 1.0
    target_extend_trigger_r: float = 0.25
    target_extend_r: float = 1.0
    pyramid_at_r: float = 1.0
    minimum_step_r: float = 0.1
    notify_step_r: float = 0.5


class ManagedPosition(Protocol):
    direction: str
    entry: float
    stop: float
    target: float

    @property
    def risk_points(self) -> float: ...

    def profit_r(self, price: float) -> float: ...


@dataclass(frozen=True)
class LevelPlan:
    stop: float
    target: float
    reasons: tuple[str, ...]


def plan_levels(
    position: ManagedPosition,
    price: float,
    config: ManagementConfig,
    *,
    decimals: int = 2,
) -> LevelPlan | None:
    """New stop/target for `position` at exit-side `price`, or None when nothing changes.

    Stops only ever tighten. BREAKEVEN marks the move that first puts the stop at or beyond
    the entry; later tightening is TRAIL. TARGET_EXTENDED pushes the target further out when
    price is close to it, so a trending winner is not capped.
    """
    risk_points = position.risk_points
    if risk_points <= 0:
        return None
    if position.profit_r(price) < config.trail_start_r:
        return None

    sign = 1.0 if position.direction == "BUY" else -1.0
    stop, target = position.stop, position.target
    reasons: list[str] = []
    trailed = round(price - sign * config.trail_distance_r * risk_points, decimals)
    if (trailed - stop) * sign >= config.minimum_step_r * risk_points:
        crosses_entry = (stop - position.entry) * sign < 0 <= (trailed - position.entry) * sign
        reasons.append("BREAKEVEN" if crosses_entry else "TRAIL")
        stop = trailed
    if (target - price) * sign <= config.target_extend_trigger_r * risk_points:
        target = round(target + sign * config.target_extend_r * risk_points, decimals)
        reasons.append("TARGET_EXTENDED")
    if not reasons:
        return None
    return LevelPlan(stop, target, tuple(reasons))


def stop_protects_entry(position: ManagedPosition) -> bool:
    sign = 1.0 if position.direction == "BUY" else -1.0
    return (position.stop - position.entry) * sign >= 0


def pyramid_ready(newest: ManagedPosition, price: float, config: ManagementConfig) -> bool:
    """Add only once the newest position is a winner whose own stop can no longer lose."""
    return newest.profit_r(price) >= config.pyramid_at_r and stop_protects_entry(newest)
