from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Literal, Protocol

from .regime import market_regime
from .invert import INVERT_MODES, invert_setup, original_reward_ok
from .ai_review import OpenAISetupReviewer
from .models import AIReview, Candle, MarketSetup
from .paper import PaperLedger
from .risk import RiskDecision, RiskEngine
from .control import read_control
from .sessions import TradingWindow
from .strategy import HigherTimeframeTrendFilter


class Strategy(Protocol):
    name: str

    @property
    def minimum_candles(self) -> int: ...

    def analyze(self, candles: list[Candle]) -> MarketSetup: ...


@dataclass(frozen=True)
class BotResult:
    setup: MarketSetup
    risk: RiskDecision
    ai_review: AIReview
    paper_events: tuple[dict[str, Any], ...]
    paper_entry_approved: bool
    observed_spread: float
    strategy_setups: tuple[MarketSetup, ...] = ()
    higher_timeframe_bias: str = "NOT_USED"
    confirm_bias: str = "NOT_USED"
    regime: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup": self.setup.to_dict(),
            "risk": self.risk.to_dict(),
            "ai_review": self.ai_review.to_dict(),
            "paper_entry_approved": self.paper_entry_approved,
            "observed_spread": self.observed_spread,
            "paper_events": list(self.paper_events),
            "strategy_setups": [setup.to_dict() for setup in self.strategy_setups],
            "higher_timeframe_bias": self.higher_timeframe_bias,
            "confirm_bias": self.confirm_bias,
            "regime": self.regime,
        }


class BotRunner:
    def __init__(
        self,
        strategy: Strategy | Sequence[Strategy],
        risk_engine: RiskEngine,
        ledger: PaperLedger,
        reviewer: OpenAISetupReviewer | None = None,
        trend_filter: HigherTimeframeTrendFilter | None = None,
        entry_window: TradingWindow | None = None,
        entry_duration: timedelta = timedelta(minutes=5),
        confirm_filter: HigherTimeframeTrendFilter | None = None,
        control_dir: Path | None = None,
        invert: str | None = None,
    ) -> None:
        if isinstance(strategy, Sequence):
            self.strategies = tuple(strategy)
        else:
            self.strategies = (strategy,)
        if not self.strategies:
            raise ValueError("At least one strategy is required")
        self.risk_engine = risk_engine
        self.ledger = ledger
        self.reviewer = reviewer
        self.trend_filter = trend_filter
        self.entry_window = entry_window
        self.entry_duration = entry_duration
        # A second, slower trend that must also agree (e.g. daily on top of the 4h filter).
        self.confirm_filter = confirm_filter
        # Where /pause and /resume write control.json for this session.
        self.control_dir = control_dir
        if invert is not None and invert not in INVERT_MODES:
            raise ValueError(f"Unknown invert mode {invert!r}")
        self.invert = invert
        # The original setup's reward-to-risk is checked before inverting (issue #69).
        self._invert_engine = (
            RiskEngine(replace(risk_engine.config, minimum_reward_to_risk=0.0)) if invert else None
        )
        # Set by the bot loop, e.g. "Weekend close in 45 minutes: no new entries."
        self.entry_block: str | None = None

    def process(
        self,
        candles: list[Candle],
        *,
        use_ai: bool,
        paper: bool,
        defer_paper_entry: bool = False,
        trend_candles: list[Candle] | None = None,
        confirm_candles: list[Candle] | None = None,
    ) -> BotResult:
        minimum_candles = max(strategy.minimum_candles for strategy in self.strategies)
        if len(candles) < minimum_candles + 1:
            raise ValueError("Not enough candles after excluding the possibly incomplete latest bar")

        # Capital.com may include the currently forming bar. Signals use completed bars only.
        completed = candles[:-1]
        latest = completed[-1]
        paper_events: list[dict[str, Any]] = []
        if paper:
            paper_events.extend(self.ledger.update_open_positions(latest))

        strategy_setups = tuple(strategy.analyze(completed) for strategy in self.strategies)
        setup = self._select_setup(strategy_setups)
        if self.invert and setup.ready:
            if not original_reward_ok(setup, self.risk_engine.config.minimum_reward_to_risk):
                setup = MarketSetup(
                    decision="NO_TRADE", timestamp=setup.timestamp,
                    reason="Reward-to-risk of the original signal is below the configured minimum.",
                    bias=setup.bias, strategy=setup.strategy, atr=setup.atr,
                )
            elif self.invert == "trend-keep":
                setup = invert_setup(setup, "mirror")
        higher_timeframe_bias: str = "NOT_USED"
        if self.trend_filter is not None:
            if trend_candles is None:
                raise ValueError("Higher-timeframe candles are required by the trend filter")
            if len(trend_candles) < self.trend_filter.minimum_candles + 1:
                raise ValueError(
                    "Not enough higher-timeframe candles after excluding the incomplete bar"
                )
            higher_timeframe_bias = self.trend_filter.analyze(trend_candles[:-1])
            setup = self._apply_trend_filter(setup, higher_timeframe_bias)
        confirm_bias: str = "NOT_USED"
        if self.confirm_filter is not None:
            if confirm_candles is None:
                raise ValueError("Confirming-trend candles are required by the confirm filter")
            if len(confirm_candles) < self.confirm_filter.minimum_candles + 1:
                raise ValueError(
                    "Not enough confirming-trend candles after excluding the incomplete bar"
                )
            confirm_bias = self.confirm_filter.analyze(confirm_candles[:-1])
            setup = self._apply_trend_filter(
                setup, confirm_bias, strategy="confirm_trend_filter", label="confirming"
            )
        paused = self.paused_reason()
        if paused and setup.ready:
            setup = MarketSetup(
                decision="NO_TRADE",
                timestamp=setup.timestamp,
                reason=f"{paused} The {setup.strategy} {setup.decision} signal was skipped.",
                bias=setup.bias,
                strategy="paused",
                atr=setup.atr,
                fast_ema=setup.fast_ema,
                slow_ema=setup.slow_ema,
            )
        if self.entry_window is not None:
            # A signal is acted on when its candle closes.
            setup = self._apply_entry_window(
                setup, self.entry_window.block_reason(latest.timestamp + self.entry_duration)
            )
        if self.invert in ("mirror", "exact"):
            setup = invert_setup(setup, self.invert)
        risk = (self._invert_engine or self.risk_engine).evaluate(
            setup,
            spread=latest.spread,
            state=self.ledger.risk_state(latest.timestamp),
        )

        if use_ai:
            if self.reviewer is None:
                raise RuntimeError("AI review requested but OPENAI_API_KEY or OPENAI_MODEL is missing")
            if setup.ready:
                try:
                    ai_review = self.reviewer.review(setup, completed)
                except Exception:
                    ai_review = AIReview(
                        verdict="REJECT",
                        confidence=0.0,
                        concerns=("The AI reviewer was unavailable.",),
                        rationale="The candidate was rejected closed because AI review failed.",
                    )
            else:
                ai_review = AIReview.not_run("No deterministic READY candidate to review.")
        else:
            ai_review = AIReview.not_run("AI review was not enabled for this run.")

        paper_entry_approved = risk.approved and (
            not use_ai or ai_review.verdict == "APPROVE"
        )
        if paper and paper_entry_approved and not defer_paper_entry:
            paper_events.append(self.ledger.open_position(setup, risk))

        result = BotResult(
            setup=setup,
            risk=risk,
            ai_review=ai_review,
            paper_events=tuple(paper_events),
            paper_entry_approved=paper_entry_approved,
            observed_spread=latest.spread,
            strategy_setups=strategy_setups,
            higher_timeframe_bias=higher_timeframe_bias,
            confirm_bias=confirm_bias,
            regime=self._regime(completed, higher_timeframe_bias),
        )
        self.ledger.record_decision(
            {
                "timestamp": latest.timestamp.isoformat(),
                "paper_mode": paper,
                "result": result.to_dict(),
            }
        )
        return result

    @staticmethod
    def _regime(completed: list[Candle], trend_bias: str) -> dict[str, Any]:
        """Market-state tags for later analysis (#77); never affects a decision."""
        try:
            regime = market_regime(completed)
        except (ValueError, ZeroDivisionError):
            return {}
        if regime:
            regime["higher_timeframe_bias"] = trend_bias
        return regime

    def paused_reason(self) -> str | None:
        if self.entry_block:
            return self.entry_block
        if self.control_dir is None:
            return None
        control = read_control(self.control_dir)
        if not control.paused:
            return None
        return f"New entries are paused (since {control.changed_at or 'unknown'})."

    @staticmethod
    def _apply_entry_window(setup: MarketSetup, blocked: str | None) -> MarketSetup:
        if not setup.ready or blocked is None:
            return setup
        return MarketSetup(
            decision="NO_TRADE",
            timestamp=setup.timestamp,
            reason=f"{blocked} The {setup.strategy} {setup.decision} signal was blocked.",
            bias=setup.bias,
            strategy="trading_hours",
            atr=setup.atr,
            fast_ema=setup.fast_ema,
            slow_ema=setup.slow_ema,
        )

    @staticmethod
    def _apply_trend_filter(
        setup: MarketSetup,
        trend_bias: Literal["BULLISH", "BEARISH", "NEUTRAL"],
        *,
        strategy: str = "trend_filter",
        label: str = "higher-timeframe",
    ) -> MarketSetup:
        allowed = (
            setup.decision == "READY_LONG" and trend_bias == "BULLISH"
        ) or (
            setup.decision == "READY_SHORT" and trend_bias == "BEARISH"
        )
        if not setup.ready or allowed:
            return setup
        return MarketSetup(
            decision="NO_TRADE",
            timestamp=setup.timestamp,
            reason=(
                f"The {label} trend is {trend_bias}; the {setup.strategy} "
                f"{setup.decision} signal was blocked."
            ),
            bias=trend_bias,
            strategy=strategy,
            atr=setup.atr,
            fast_ema=setup.fast_ema,
            slow_ema=setup.slow_ema,
        )

    @staticmethod
    def _select_setup(setups: tuple[MarketSetup, ...]) -> MarketSetup:
        ready = [setup for setup in setups if setup.ready]
        directions = {setup.direction for setup in ready}
        if len(directions) > 1:
            reference = setups[0]
            return MarketSetup(
                decision="NO_TRADE",
                timestamp=reference.timestamp,
                reason="Strategies produced opposing READY signals; execution was blocked.",
                bias="NEUTRAL",
                strategy="strategy_conflict",
                atr=reference.atr,
                fast_ema=reference.fast_ema,
                slow_ema=reference.slow_ema,
            )
        if ready:
            return ready[0]
        watching = [setup for setup in setups if setup.decision.startswith("WATCH_")]
        if watching:
            return watching[0]
        reference = setups[0]
        decisions = ", ".join(f"{setup.strategy}={setup.decision}" for setup in setups)
        return MarketSetup(
            decision="NO_TRADE",
            timestamp=reference.timestamp,
            reason=f"No strategy is ready ({decisions}).",
            bias=reference.bias,
            strategy="multi_strategy",
            atr=reference.atr,
            fast_ema=reference.fast_ema,
            slow_ema=reference.slow_ema,
        )
