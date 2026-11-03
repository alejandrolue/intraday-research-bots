from __future__ import annotations

from datetime import datetime, timezone

from goldbot.models import MarketSetup
from goldbot.risk import RiskConfig, RiskEngine, RiskState


def ready_setup() -> MarketSetup:
    return MarketSetup(
        decision="READY_LONG",
        timestamp=datetime(2026, 9, 28, tzinfo=timezone.utc),
        reason="test",
        bias="BULLISH",
        entry=100.0,
        stop=98.0,
        target=104.0,
    )


def test_risk_engine_sizes_from_fixed_risk_budget() -> None:
    engine = RiskEngine(RiskConfig(risk_per_trade=0.01, point_value_per_unit=2.0))
    state = RiskState(10_000, 0, 0)

    decision = engine.evaluate(ready_setup(), spread=0.5, state=state)

    assert decision.approved
    assert decision.risk_budget == 100
    assert decision.units == 25
    assert decision.expected_reward_to_risk == 2


def test_risk_engine_rejects_excessive_spread() -> None:
    engine = RiskEngine(RiskConfig(max_spread=0.8))
    state = RiskState(10_000, 0, 0)

    decision = engine.evaluate(ready_setup(), spread=1.2, state=state)

    assert not decision.approved
    assert "Spread" in decision.reason



def test_risk_engine_allows_stacking_up_to_the_position_cap() -> None:
    engine = RiskEngine(RiskConfig(max_positions=3))

    two_open = RiskState(10_000, 0, 0, open_positions=2, open_direction="BUY")
    three_open = RiskState(10_000, 0, 0, open_positions=3, open_direction="BUY")

    assert engine.evaluate(ready_setup(), spread=0.5, state=two_open).approved
    blocked = engine.evaluate(ready_setup(), spread=0.5, state=three_open)
    assert not blocked.approved
    assert "cap is 3" in blocked.reason


def test_risk_engine_blocks_entries_against_open_positions() -> None:
    engine = RiskEngine(RiskConfig(max_positions=3))
    state = RiskState(10_000, 0, 0, open_positions=1, open_direction="SELL")

    decision = engine.evaluate(ready_setup(), spread=0.5, state=state)

    assert not decision.approved
    assert "opposite" in decision.reason


def test_daily_risk_cap_scales_to_realized_loss_and_open_stop_risk() -> None:
    engine = RiskEngine(
        RiskConfig(
            risk_per_trade=0.02,
            max_daily_loss=0.10,
            max_positions=5,
            cap_daily_risk=True,
        )
    )
    state = RiskState(
        10_800,
        -200,
        0,
        open_positions=4,
        open_direction="BUY",
        open_risk=800,
    )

    decision = engine.evaluate(ready_setup(), spread=0.5, state=state)

    assert decision.approved
    assert decision.risk_budget == 80
    assert decision.units == 40
    assert "reduced" in decision.reason


def test_daily_risk_cap_blocks_when_realized_and_open_risk_use_the_limit() -> None:
    engine = RiskEngine(
        RiskConfig(max_daily_loss=0.10, max_positions=5, cap_daily_risk=True)
    )
    state = RiskState(
        10_800,
        -200,
        0,
        open_positions=4,
        open_direction="BUY",
        open_risk=880,
    )

    decision = engine.evaluate(ready_setup(), spread=0.5, state=state)

    assert not decision.approved
    assert "open stop-risk" in decision.reason
