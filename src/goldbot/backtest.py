"""Replay the live strategies, filters, risk rules and trade management on historical candles.

Conventions (chosen to be conservative, and to match live execution as closely as bar data
allows):

- Candles are mid prices; each candle carries its bid/ask spread. Longs fill at the ask and
  exit at the bid, shorts the reverse.
- A signal is evaluated when its candle closes and filled at the next candle's open, with the
  same drift rule as live: stop and target shift by the gap, and entries that moved more than
  `max_entry_drift_r` are skipped.
- When a candle touches both stop and target, the stop is assumed to fill first. Gaps through
  a stop fill at the open.
- Trailing, target extension and adding to winners are evaluated at candle closes. Live
  management runs every 15 seconds, so it trails a little tighter than this simulation.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, time
from zoneinfo import ZoneInfo
from typing import Any, Literal

from .indicators import adx
from .evolution_strategy import DailyEvolutionStrategy, FiveMinuteEvolutionStrategy
from .volume_pullback import Ema21TrendFilter
from .invert import invert_setup, original_reward_ok
from .management import ManagementConfig, plan_levels, pyramid_ready
from .models import Candle, MarketSetup
from .risk import RiskConfig, RiskEngine, RiskState
from .runner import BotRunner
from .sessions import TradingWindow, resolution_delta
from .strategy import (
    DemandZoneStrategy,
    HigherTimeframeTrendFilter,
    PullbackStrategy,
    RetestStrategy,
)


STRATEGIES = {
    "5m_evolution": FiveMinuteEvolutionStrategy,
    "5m_evolution_daily": DailyEvolutionStrategy,
    "breakout_retest": RetestStrategy,
    "ema_pullback": PullbackStrategy,
    "demand_zone_rejection": DemandZoneStrategy,
}


@dataclass(frozen=True)
class BacktestConfig:
    resolution: str = "MINUTE_5"
    trend_resolution: str | None = "HOUR"
    window: TradingWindow | None = field(default_factory=TradingWindow)
    management: ManagementConfig | None = field(default_factory=ManagementConfig)
    risk: RiskConfig = field(
        default_factory=lambda: RiskConfig(max_trades_per_day=15, max_positions=3)
    )
    strategies: tuple[str, ...] = tuple(STRATEGIES)
    lookback: int = 300
    trend_lookback: int = 150
    units: float = 0.1
    max_entry_drift_r: float = 0.5
    starting_balance: float = 10_000.0
    directions: Literal["both", "long", "short"] = "both"
    # A second higher-timeframe trend that must agree with the signal (e.g. DAY on top of HOUR_4).
    confirm_trend_resolution: str | None = None
    # Apply the confirming trend to short signals only (longs need just the main filter).
    confirm_shorts_only: bool = False
    # Skip signals when ADX on the entry candles is below this (sideways market).
    min_adx: float | None = None
    adx_period: int = 14
    label: str = ""
    # "ema20_50" (default) or "ema21_spec" (issue #63).
    trend_filter: str = "ema20_50"
    # Issue #69: trade the opposite of each signal ("mirror", "exact" or "trend-keep").
    invert: str | None = None
    # Day trading: (time zone, hour, minute) in local exchange time. Positions still open at a candle
    # ending at or after it are closed at that candle's close; no entries are taken after it.
    flat_at: tuple[str, int, int] | None = None


@dataclass
class SimPosition:
    direction: Literal["BUY", "SELL"]
    entry: float
    stop: float
    target: float
    units: float
    opened_at: datetime
    strategy: str
    role: Literal["signal", "add"]
    initial_stop: float

    @property
    def risk_points(self) -> float:
        return abs(self.entry - self.initial_stop)

    def profit_r(self, price: float) -> float:
        if self.risk_points <= 0:
            return 0.0
        sign = 1.0 if self.direction == "BUY" else -1.0
        return (price - self.entry) * sign / self.risk_points


@dataclass(frozen=True)
class Trade:
    strategy: str
    role: str
    direction: str
    opened_at: datetime
    closed_at: datetime
    entry: float
    exit: float
    initial_stop: float
    exit_reason: str
    r: float
    pnl: float


@dataclass
class _PendingEntry:
    setup: MarketSetup
    units: float
    role: Literal["signal", "add"]


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: list[Trade]
    start: datetime | None
    end: datetime | None
    bars: int
    skipped: Counter[str]

    def metrics(self) -> dict[str, Any]:
        trades = sorted(self.trades, key=lambda trade: trade.closed_at)
        rs = [trade.r for trade in trades]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r < 0]
        equity = peak = drawdown = 0.0
        for r in rs:
            equity += r
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        weeks = (
            max((self.end - self.start).total_seconds() / 604_800, 1e-9)
            if self.start and self.end
            else 0.0
        )
        halves: tuple[float, float] = (0.0, 0.0)
        if self.start and self.end and trades:
            middle = self.start + (self.end - self.start) / 2
            halves = (
                sum(trade.r for trade in trades if trade.closed_at < middle),
                sum(trade.r for trade in trades if trade.closed_at >= middle),
            )
        by_strategy: dict[str, dict[str, float]] = {}
        for trade in trades:
            row = by_strategy.setdefault(trade.strategy, {"trades": 0, "total_r": 0.0, "wins": 0})
            row["trades"] += 1
            row["total_r"] += trade.r
            row["wins"] += trade.r > 0
        return {
            "label": self.config.label,
            "trades": len(rs),
            "win_rate": len(wins) / len(rs) if rs else 0.0,
            "average_r": sum(rs) / len(rs) if rs else 0.0,
            "total_r": sum(rs),
            "profit_factor": sum(wins) / -sum(losses) if losses else math.inf if wins else 0.0,
            "max_drawdown_r": drawdown,
            "trades_per_week": len(rs) / weeks if weeks else 0.0,
            "pnl": sum(trade.pnl for trade in trades),
            "first_half_r": halves[0],
            "second_half_r": halves[1],
            "adds": sum(1 for trade in trades if trade.role == "add"),
            "by_strategy": by_strategy,
            "exit_reasons": dict(Counter(trade.exit_reason for trade in trades)),
            "skipped": dict(self.skipped),
        }


def run_backtest(
    candles: list[Candle],
    trend_candles: list[Candle] | None,
    config: BacktestConfig,
    *,
    strategies: Sequence[Any] | None = None,
    confirm_candles: list[Candle] | None = None,
) -> BacktestResult:
    candles = sorted(candles, key=lambda candle: candle.timestamp)
    strategies = list(strategies or [STRATEGIES[name]() for name in config.strategies])
    trend_filter = (
        (Ema21TrendFilter() if config.trend_filter == "ema21_spec" else HigherTimeframeTrendFilter())
        if config.trend_resolution
        else None
    )
    trend = sorted(trend_candles or [], key=lambda candle: candle.timestamp)
    trend_duration = resolution_delta(config.trend_resolution) if config.trend_resolution else None
    duration = resolution_delta(config.resolution)
    risk_engine = RiskEngine(config.risk)
    flat_zone = ZoneInfo(config.flat_at[0]) if config.flat_at else None
    flat_time = time(config.flat_at[1], config.flat_at[2]) if config.flat_at else None
    # The original setup's reward-to-risk is checked before inverting (issue #69).
    invert_engine = RiskEngine(replace(config.risk, minimum_reward_to_risk=0.0))
    warmup = max(config.lookback, max(strategy.minimum_candles for strategy in strategies))

    open_positions: list[SimPosition] = []
    trades: list[Trade] = []
    pending: list[_PendingEntry] = []
    skipped: Counter[str] = Counter()
    balance = config.starting_balance
    day = None
    daily_pnl = 0.0
    trades_today = 0
    trend_index = -1
    trend_bias: dict[int, str] = {}
    confirm = sorted(confirm_candles or [], key=lambda candle: candle.timestamp)
    confirm_duration = (
        resolution_delta(config.confirm_trend_resolution)
        if config.confirm_trend_resolution
        else None
    )
    confirm_index = -1
    confirm_bias: dict[int, str] = {}

    def close(position: SimPosition, price: float, when: datetime, reason: str) -> None:
        nonlocal balance, daily_pnl
        sign = 1.0 if position.direction == "BUY" else -1.0
        points = (price - position.entry) * sign
        pnl = points * position.units
        balance += pnl
        daily_pnl += pnl
        if reason == "STOP" and (position.stop - position.entry) * sign >= 0:
            reason = "TRAILING_STOP"
        trades.append(
            Trade(
                strategy=position.strategy,
                role=position.role,
                direction=position.direction,
                opened_at=position.opened_at,
                closed_at=when,
                entry=position.entry,
                exit=price,
                initial_stop=position.initial_stop,
                exit_reason=reason,
                r=points / position.risk_points if position.risk_points else 0.0,
                pnl=pnl,
            )
        )
        open_positions.remove(position)

    def risk_state() -> RiskState:
        open_risk = sum(
            max(
                0.0,
                (position.entry - position.stop)
                * (1.0 if position.direction == "BUY" else -1.0),
            )
            * position.units
            * config.risk.point_value_per_unit
            for position in open_positions
        )
        return RiskState(
            balance=balance,
            daily_realized_pnl=daily_pnl,
            trades_today=trades_today,
            open_positions=len(open_positions) + len(pending),
            open_direction=(
                open_positions[0].direction
                if open_positions
                else pending[0].setup.direction if pending else None
            ),
            open_risk=open_risk,
        )

    for index in range(warmup, len(candles)):
        bar = candles[index]
        half = bar.spread / 2
        if bar.timestamp.date() != day:
            day = bar.timestamp.date()
            daily_pnl = 0.0
            trades_today = 0

        # 1. Fill entries decided at the previous close, at this bar's open.
        for order in pending:
            setup = order.setup
            assert setup.entry is not None and setup.stop is not None and setup.target is not None
            sign = 1.0 if setup.direction == "BUY" else -1.0
            fill = bar.open + sign * half
            drift = fill - setup.entry
            risk_distance = abs(setup.entry - setup.stop)
            stop, target = setup.stop, setup.target
            if order.role == "signal":
                if abs(drift) > config.max_entry_drift_r * risk_distance:
                    skipped["entry_drift"] += 1
                    continue
                stop, target = stop + drift, target + drift
            if (fill - stop) * sign <= 0 or (target - fill) * sign <= 0:
                skipped["levels_crossed_before_fill"] += 1
                continue
            assert setup.direction is not None
            open_positions.append(
                SimPosition(
                    direction=setup.direction,
                    entry=fill,
                    stop=stop,
                    target=target,
                    units=order.units,
                    opened_at=bar.timestamp,
                    strategy=setup.strategy,
                    role=order.role,
                    initial_stop=stop,
                )
            )
            trades_today += 1
        pending = []

        # 2. Stops and targets during this bar (stop first when both are touched).
        for position in list(open_positions):
            if position.direction == "BUY":
                open_exit, low, high = bar.open - half, bar.low - half, bar.high - half
                if open_exit <= position.stop:
                    close(position, open_exit, bar.timestamp, "STOP")
                elif low <= position.stop:
                    close(position, position.stop, bar.timestamp, "STOP")
                elif high >= position.target:
                    close(position, max(position.target, open_exit), bar.timestamp, "TARGET")
            else:
                open_exit, low, high = bar.open + half, bar.low + half, bar.high + half
                if open_exit >= position.stop:
                    close(position, open_exit, bar.timestamp, "STOP")
                elif high >= position.stop:
                    close(position, position.stop, bar.timestamp, "STOP")
                elif low <= position.target:
                    close(position, min(position.target, open_exit), bar.timestamp, "TARGET")

        closes_at = bar.timestamp + duration

        # 2b. Day trading: flat at the configured local time.
        if flat_time is not None and open_positions and _past_flat(closes_at, flat_zone, flat_time):
            for position in list(open_positions):
                exit_price = bar.close - half if position.direction == "BUY" else bar.close + half
                close(position, exit_price, bar.timestamp, "END_OF_DAY")

        # 3. Trade management at the close.
        if config.management and open_positions:
            for position in open_positions:
                exit_price = bar.close - half if position.direction == "BUY" else bar.close + half
                plan = plan_levels(position, exit_price, config.management)
                if plan:
                    position.stop, position.target = plan.stop, plan.target
            newest = open_positions[-1]
            exit_price = bar.close - half if newest.direction == "BUY" else bar.close + half
            if (
                pyramid_ready(newest, exit_price, config.management)
                and risk_engine.entry_block_reason(risk_state(), newest.direction) is None
                and (config.window is None or config.window.block_reason(closes_at) is None)
            ):
                pending.append(
                    _PendingEntry(
                        MarketSetup(
                            decision="READY_LONG" if newest.direction == "BUY" else "READY_SHORT",
                            timestamp=bar.timestamp,
                            reason="Add to winner",
                            bias="NEUTRAL",
                            strategy=newest.strategy,
                            entry=bar.close,
                            stop=newest.stop,
                            target=newest.target,
                        ),
                        newest.units,
                        "add",
                    )
                )

        # 4. New signals at the close.
        if config.window is not None and config.window.block_reason(closes_at):
            continue
        window = candles[index - config.lookback + 1 : index + 1]
        setups = tuple(strategy.analyze(window) for strategy in strategies)
        setup = BotRunner._select_setup(setups)
        if not setup.ready:
            continue
        if config.invert:
            if not original_reward_ok(setup, config.risk.minimum_reward_to_risk):
                skipped["risk: Reward-to-risk is below the configured minimum"] += 1
                continue
            if config.invert == "trend-keep":
                setup = invert_setup(setup, "mirror")
        if trend_filter is not None and trend_duration is not None:
            while (
                trend_index + 1 < len(trend)
                and trend[trend_index + 1].timestamp + trend_duration <= closes_at
            ):
                trend_index += 1
            if trend_index not in trend_bias:
                completed = trend[max(0, trend_index - config.trend_lookback + 1) : trend_index + 1]
                trend_bias[trend_index] = (
                    trend_filter.analyze(completed)
                    if len(completed) >= trend_filter.minimum_candles
                    else "NEUTRAL"
                )
            setup = BotRunner._apply_trend_filter(setup, trend_bias[trend_index])  # type: ignore[arg-type]
            if not setup.ready:
                skipped["trend_filter"] += 1
                continue
        if flat_time is not None and _past_flat(closes_at, flat_zone, flat_time):
            skipped["after_day_close"] += 1
            continue
        wanted = {"both": ("BUY", "SELL"), "long": ("BUY",), "short": ("SELL",)}[config.directions]
        if setup.direction not in wanted:
            skipped["direction"] += 1
            continue
        if (
            trend_filter is not None
            and confirm_duration is not None
            and (setup.direction == "SELL" or not config.confirm_shorts_only)
        ):
            while (
                confirm_index + 1 < len(confirm)
                and confirm[confirm_index + 1].timestamp + confirm_duration <= closes_at
            ):
                confirm_index += 1
            if confirm_index not in confirm_bias:
                completed = confirm[
                    max(0, confirm_index - config.trend_lookback + 1) : confirm_index + 1
                ]
                confirm_bias[confirm_index] = (
                    trend_filter.analyze(completed)
                    if len(completed) >= trend_filter.minimum_candles
                    else "NEUTRAL"
                )
            expected = "BULLISH" if setup.direction == "BUY" else "BEARISH"
            if confirm_bias[confirm_index] != expected:
                skipped["confirm_trend"] += 1
                continue
        if config.min_adx is not None and adx(window, config.adx_period) < config.min_adx:
            skipped["adx_below_minimum"] += 1
            continue
        if config.invert in ("mirror", "exact"):
            setup = invert_setup(setup, config.invert)
        decision = (invert_engine if config.invert else risk_engine).evaluate(
            setup, spread=bar.spread, state=risk_state()
        )
        if not decision.approved:
            reason = decision.reason.split(";")[0].rstrip(".")
            if reason.startswith("Spread"):
                reason = "spread above maximum"
            skipped["risk: " + reason] += 1
            continue
        units = math.floor(min(decision.units, config.units) * 100) / 100
        if units <= 0:
            skipped["size_below_minimum"] += 1
            continue
        pending.append(_PendingEntry(setup, units, "signal"))

    if candles and open_positions:
        last = candles[-1]
        for position in list(open_positions):
            half = last.spread / 2
            price = last.close - half if position.direction == "BUY" else last.close + half
            close(position, price, last.timestamp, "END_OF_DATA")

    return BacktestResult(
        config=config,
        trades=trades,
        start=candles[warmup].timestamp if len(candles) > warmup else None,
        end=candles[-1].timestamp if candles else None,
        bars=max(0, len(candles) - warmup),
        skipped=skipped,
    )


def _past_flat(moment: datetime, zone: ZoneInfo | None, flat: time) -> bool:
    """True when `moment` is at or after the daily flat time in the exchange's time zone."""
    return moment.astimezone(zone).time() >= flat


def with_label(config: BacktestConfig, **changes: Any) -> BacktestConfig:
    updated = replace(config, **changes)
    trend = updated.trend_resolution or "none"
    if updated.confirm_trend_resolution:
        trend += f"+{updated.confirm_trend_resolution}"
        if updated.confirm_shorts_only:
            trend += "(shorts)"
    parts = [
        f"filter {trend}",
        "hours london-ny" if updated.window else "hours all",
        "managed" if updated.management else "fixed stop/target",
    ]
    if updated.directions != "both":
        parts.append(f"{updated.directions}-only")
    if updated.min_adx is not None:
        parts.append(f"ADX>={updated.min_adx:g}")
    return replace(updated, label=", ".join(parts))


def format_backtest_table(rows: list[dict[str, Any]]) -> str:
    header = (
        f"{'configuration':<64} {'trades':>6} {'/week':>6} {'win%':>5} {'avg R':>7} "
        f"{'total R':>8} {'PF':>5} {'maxDD R':>8} {'1st half':>9} {'2nd half':>9}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        pf = row["profit_factor"]
        lines.append(
            f"{row['label']:<64} {row['trades']:>6} {row['trades_per_week']:>6.1f} "
            f"{row['win_rate']:>5.0%} {row['average_r']:>+7.3f} {row['total_r']:>+8.1f} "
            f"{('inf' if pf == math.inf else f'{pf:.2f}'):>5} {row['max_drawdown_r']:>8.1f} "
            f"{row['first_half_r']:>+9.1f} {row['second_half_r']:>+9.1f}"
        )
    return "\n".join(lines)


def format_backtest_detail(row: dict[str, Any]) -> str:
    lines = [f"Detail: {row['label']}"]
    for name, stats in sorted(row["by_strategy"].items()):
        rate = stats["wins"] / stats["trades"] if stats["trades"] else 0
        lines.append(
            f"  {name:<24} {stats['trades']:>5} trades  {rate:>4.0%} win  "
            f"{stats['total_r']:>+7.1f}R  ({stats['total_r'] / stats['trades']:+.3f}R/trade)"
        )
    lines.append("  exits: " + ", ".join(f"{k} {v}" for k, v in sorted(row["exit_reasons"].items())))
    if row["adds"]:
        lines.append(f"  adds to winners: {row['adds']}")
    if row["skipped"]:
        lines.append(
            "  skipped: " + ", ".join(f"{k} {v}" for k, v in sorted(row["skipped"].items()))
        )
    return "\n".join(lines)
