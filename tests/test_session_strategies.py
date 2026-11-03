from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from goldbot.models import Candle
from goldbot.session_strategies import SessionStrategy, SessionVariant

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
import five_minute_strategies as research  # noqa: E402


def random_walk(days: int = 30, seed: int = 7) -> list[Candle]:
    rng = random.Random(seed)
    start = datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc)  # spans the US DST change
    candles, price = [], 3000.0
    for index in range(days * 288):
        timestamp = start + timedelta(minutes=5 * index)
        if timestamp.weekday() >= 5:
            continue
        open_ = price
        close = open_ + rng.gauss(0, 1.2)
        high = max(open_, close) + abs(rng.gauss(0, 0.8))
        low = min(open_, close) - abs(rng.gauss(0, 0.8))
        candles.append(Candle(timestamp, open_, high, low, close, spread=0.3))
        price = close
    return candles


@pytest.mark.parametrize("name", ["orb-ny-30-rr2", "orb-london-60-rr1.6", "rolling_sweep-ny-30-rr2"])
def test_live_copy_matches_the_research_strategy_signal_for_signal(name: str) -> None:
    variant = SessionVariant.parse(name)
    live = SessionStrategy(variant)
    reference = research.SessionStrategy(
        research.Variant(variant.family, variant.session, variant.minutes, variant.reward, None)
    )
    candles = random_walk()
    ready = 0
    for index in range(300, len(candles)):
        window = candles[index - 299 : index + 1]
        a, b = live.analyze(window), reference.analyze(window)
        assert (a.decision, a.entry, a.stop, a.target) == (b.decision, b.entry, b.stop, b.target)
        ready += a.ready
    assert ready > 10  # the comparison exercised real signals, not only NO_TRADE


def test_variant_names_parse_and_reject_typos() -> None:
    variant = SessionVariant.parse("orb-ny-30-rr2")
    assert (variant.family, variant.session, variant.minutes, variant.reward) == ("orb", "ny", 30, 2.0)
    assert variant.id == "orb-ny-30-rr2"
    for bad in ("orb-ny-45-rr2", "fade-ny-30-rr2", "orb-sydney-30-rr2"):
        with pytest.raises(ValueError):
            SessionVariant.parse(bad)


def test_session_options_select_strategies_limits_and_fixed_exits() -> None:
    from goldbot.cli import _parser, _runner, _settings

    args = _parser().parse_args([
        "watch", "--session", "fwd", "--strategies", "orb-ny-30-rr2",
        "--trend-resolution", "HOUR_4", "--max-positions", "1", "--max-trades-per-day", "3",
        "--no-manage",
    ])
    settings = _settings(args)

    assert settings.strategies == ("orb-ny-30-rr2",)
    assert settings.max_positions_per_session == 1
    assert settings.max_trades_per_day == 3
    assert settings.manage_trades is False
    runner = _runner(settings, use_ai=False)
    assert [strategy.name for strategy in runner.strategies] == ["orb-ny-30-rr2"]
    assert runner.risk_engine.config.max_positions == 1
    assert runner.risk_engine.config.max_trades_per_day == 3

    default = _settings(_parser().parse_args(["watch", "--session", "fwd"]))
    assert default.manage_trades is True
    assert default.strategies == ("breakout_retest", "ema_pullback", "demand_zone_rejection")
    with pytest.raises(ValueError):
        _settings(_parser().parse_args(["watch", "--strategies", "orb-ny-45-rr2"]))


def test_demo_execution_only_allows_approved_markets(monkeypatch) -> None:
    from dataclasses import replace as update

    from goldbot.cli import _demo_executor, _parser, _runner, _settings

    class Client:
        def get_preferences(self) -> dict:
            return {"hedgingMode": False}

    args = _parser().parse_args([
        "watch", "--session", "fwd", "--epic", "US100", "--max-spread", "6",
        "--strategies", "orb-nycash-15-rr2",
    ])
    settings = update(_settings(args), capital_demo_execution_enabled=True)
    assert settings.capital_epic == "US100"
    assert settings.max_spread == 6.0
    runner = _runner(settings, use_ai=False)
    assert runner.risk_engine.config.max_spread == 6.0

    executor = _demo_executor(settings, Client(), runner)  # type: ignore[arg-type]
    assert executor.allowed_epic == "US100"

    with pytest.raises(RuntimeError, match="approved markets"):
        _demo_executor(update(settings, capital_epic="SILVER"), Client(), runner)  # type: ignore[arg-type]


def one_minute_session(minutes_after_open: int, closes: list[float]) -> list[Candle]:
    from zoneinfo import ZoneInfo

    opened = datetime(2026, 7, 14, 9, 30, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    warm = [Candle(opened - timedelta(minutes=30 - i), 100, 100.5, 99.5, 100, spread=0.3) for i in range(30)]
    session = []
    for i, close in enumerate(closes):
        session.append(Candle(opened + timedelta(minutes=i), close, close + 0.2, close - 0.2, close, spread=0.3))
    return warm + session


def test_article_orb_stops_at_range_side_or_midpoint_on_one_minute_candles() -> None:
    # 5-minute range 99.8-100.7 on 1-minute candles, then a close above the range high.
    closes = [100, 100.3, 100.5, 100.1, 100.2, 100.4, 101.2]
    side = SessionStrategy(SessionVariant.parse("orbside-nycash-5-rr2"), bar_minutes=1)
    mid = SessionStrategy(SessionVariant.parse("orbmid-nycash-5-rr1.5"), bar_minutes=1)

    a = side.analyze(one_minute_session(6, closes))
    b = mid.analyze(one_minute_session(6, closes))

    assert a.decision == b.decision == "READY_LONG"
    assert round(a.stop, 2) == 99.8                      # opposite side of the range
    assert round(a.target, 2) == round(101.2 + 2 * (101.2 - 99.8), 2)
    assert round(b.stop, 2) == round((100.7 + 99.8) / 2, 2)   # range midpoint
    assert round(b.target - b.entry, 4) == round(1.5 * (b.entry - b.stop), 4)


def test_article_orb_needs_a_range_that_fits_the_candle_size() -> None:
    with pytest.raises(ValueError):
        SessionStrategy(SessionVariant.parse("orbside-nycash-5-rr2"), bar_minutes=15)


def test_sizing_options_per_session_with_safety_limits() -> None:
    from goldbot.cli import _parser, _runner, _settings

    args = _parser().parse_args([
        "watch", "--session", "learn", "--risk-per-trade", "0.01", "--max-size", "100",
        "--max-daily-loss", "0.10", "--max-trades-per-day", "50", "--hours", "all",
    ])
    settings = _settings(args)
    assert (settings.risk_per_trade, settings.capital_max_demo_size, settings.max_daily_loss) == (0.01, 100.0, 0.10)
    config = _runner(settings, use_ai=False).risk_engine.config
    assert (config.risk_per_trade, config.max_daily_loss, config.max_trades_per_day) == (0.01, 0.10, 50)

    for bad in (["--risk-per-trade", "0.2"], ["--max-daily-loss", "0.5"], ["--max-size", "0"]):
        with pytest.raises(ValueError):
            _settings(_parser().parse_args(["watch", "--session", "learn", *bad]))
