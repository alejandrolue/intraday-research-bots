from __future__ import annotations

from datetime import datetime, timezone

from goldbot.models import MarketSetup
from goldbot.runner import BotRunner


NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def ready(decision: str) -> MarketSetup:
    return MarketSetup(
        decision=decision,  # type: ignore[arg-type]
        timestamp=NOW,
        reason="test",
        bias="BULLISH" if decision == "READY_LONG" else "BEARISH",
        strategy="ema_pullback",
        entry=100,
        stop=98 if decision == "READY_LONG" else 102,
        target=103 if decision == "READY_LONG" else 97,
    )


def test_hourly_filter_allows_aligned_ready_signal() -> None:
    setup = ready("READY_LONG")

    assert BotRunner._apply_trend_filter(setup, "BULLISH") is setup


def test_hourly_filter_blocks_opposing_ready_signal() -> None:
    setup = BotRunner._apply_trend_filter(ready("READY_LONG"), "BEARISH")

    assert setup.decision == "NO_TRADE"
    assert setup.strategy == "trend_filter"
    assert "BEARISH" in setup.reason


class _Always:
    name = "scripted"
    minimum_candles = 1

    def __init__(self, decision: str) -> None:
        self.decision = decision

    def analyze(self, candles):  # type: ignore[no-untyped-def]
        setup = ready(self.decision)
        return MarketSetup(**{**setup.__dict__, "timestamp": candles[-1].timestamp})


class _FixedTrend:
    minimum_candles = 1

    def __init__(self, bias: str) -> None:
        self.bias = bias
        self.seen: list[int] = []

    def analyze(self, candles):  # type: ignore[no-untyped-def]
        self.seen.append(len(candles))
        return self.bias


def _runner(tmp_path, main_bias: str, confirm_bias: str | None):  # type: ignore[no-untyped-def]
    from goldbot.paper import PaperLedger
    from goldbot.risk import RiskConfig, RiskEngine

    ledger = PaperLedger(
        tmp_path / "state.json", tmp_path / "journal.jsonl",
        starting_balance=10_000, point_value_per_unit=1.0,
    )
    confirm = _FixedTrend(confirm_bias) if confirm_bias else None
    runner = BotRunner(
        _Always("READY_LONG"),
        RiskEngine(RiskConfig(max_positions=3)),
        ledger,
        trend_filter=_FixedTrend(main_bias),  # type: ignore[arg-type]
        confirm_filter=confirm,  # type: ignore[arg-type]
    )
    return runner, confirm


def _candles(count: int = 5):  # type: ignore[no-untyped-def]
    from datetime import timedelta

    from goldbot.models import Candle

    return [
        Candle(NOW + timedelta(hours=i), 100, 100.5, 99.5, 100, spread=0.3) for i in range(count)
    ]


def test_confirm_filter_blocks_when_the_slower_trend_disagrees(tmp_path) -> None:
    runner, _ = _runner(tmp_path, "BULLISH", "NEUTRAL")

    result = runner.process(
        _candles(), use_ai=False, paper=False, trend_candles=_candles(), confirm_candles=_candles()
    )

    assert result.setup.decision == "NO_TRADE"
    assert result.setup.strategy == "confirm_trend_filter"
    assert "confirming trend is NEUTRAL" in result.setup.reason
    assert result.confirm_bias == "NEUTRAL"
    assert not result.paper_entry_approved


def test_confirm_filter_passes_when_both_trends_agree(tmp_path) -> None:
    runner, confirm = _runner(tmp_path, "BULLISH", "BULLISH")

    result = runner.process(
        _candles(), use_ai=False, paper=False, trend_candles=_candles(), confirm_candles=_candles(4)
    )

    assert result.setup.decision == "READY_LONG"
    assert result.risk.approved
    assert result.to_dict()["confirm_bias"] == "BULLISH"
    assert confirm is not None and confirm.seen == [3]  # the forming candle is excluded


def test_runner_without_confirm_filter_reports_not_used(tmp_path) -> None:
    runner, _ = _runner(tmp_path, "BULLISH", None)

    result = runner.process(_candles(), use_ai=False, paper=False, trend_candles=_candles())

    assert result.setup.decision == "READY_LONG"
    assert result.confirm_bias == "NOT_USED"


def test_paused_runner_skips_new_signals(tmp_path) -> None:
    from goldbot.control import write_control
    from goldbot.paper import PaperLedger
    from goldbot.risk import RiskConfig, RiskEngine
    from goldbot.runner import BotRunner

    write_control(tmp_path, paused=True, changed_by="test")
    ledger = PaperLedger(tmp_path / "state.json", tmp_path / "journal.jsonl",
                         starting_balance=10_000, point_value_per_unit=1.0)
    runner = BotRunner(_Always("READY_LONG"), RiskEngine(RiskConfig(max_positions=3)), ledger,
                       control_dir=tmp_path)

    result = runner.process(_candles(), use_ai=False, paper=False)

    assert result.setup.decision == "NO_TRADE"
    assert result.setup.strategy == "paused"
    assert not result.paper_entry_approved
