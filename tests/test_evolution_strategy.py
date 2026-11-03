from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from goldbot.cli import _parser, _settings, _strategies
from goldbot.evolution_strategy import EvolutionConfig, FiveMinuteEvolutionStrategy
from goldbot.models import Candle, MarketSetup


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(START + timedelta(minutes=5 * index), open_, high, low, close, spread=0.3)


def fakeout_candles() -> list[Candle]:
    candles = []
    for index in range(100):
        close = 100 + index * 0.02
        candles.append(candle(index, close - 0.1, close + 0.3, close - 0.3, close))
    candles[70] = candle(70, 96, 97, 94, 96)
    candles[80] = candle(80, 104, 112, 103, 104)
    candles[-3] = candle(97, 104, 110, 103, 104)
    candles[-2] = candle(98, 95.5, 96.0, 93.0, 95.7)
    candles[-1] = candle(99, 95.8, 97.2, 95.6, 97.0)
    return candles


def mirror(candles: list[Candle], axis: float = 200) -> list[Candle]:
    return [
        Candle(
            item.timestamp,
            axis - item.open,
            axis - item.low,
            axis - item.high,
            axis - item.close,
            item.volume,
            item.spread,
        )
        for item in candles
    ]


def strategy() -> FiveMinuteEvolutionStrategy:
    return FiveMinuteEvolutionStrategy(EvolutionConfig(window=40, radius=2, reward_to_risk=1.5))


def test_failed_break_fakeout_is_symmetric() -> None:
    long = strategy().analyze(fakeout_candles())
    short = strategy().analyze(mirror(fakeout_candles()))

    assert long.direction == "BUY"
    assert short.direction == "SELL"
    assert long.strategy == short.strategy == "5m_evolution_fakeout"
    assert long.stop is not None and long.zone_low is not None and long.stop < long.zone_low
    assert short.stop is not None and short.zone_high is not None and short.stop > short.zone_high


def test_close_beyond_zone_then_reclaim_is_a_fakeout() -> None:
    candles = fakeout_candles()
    candles[-2] = candle(98, 95.0, 95.5, 92.8, 93.4)
    candles[-1] = candle(99, 93.5, 96.8, 93.3, 96.5)

    setup = strategy().analyze(candles)

    assert setup.direction == "BUY"
    assert setup.strategy == "5m_evolution_fakeout"


class ScriptedOriginal:
    def __init__(self, direction: str) -> None:
        self.direction = direction

    def analyze(self, candles: list[Candle]) -> MarketSetup:
        bar = candles[-1]
        sign = 1 if self.direction == "BUY" else -1
        return MarketSetup(
            "READY_LONG" if self.direction == "BUY" else "READY_SHORT",
            bar.timestamp,
            "scripted original 5m-learn signal",
            "BULLISH" if self.direction == "BUY" else "BEARISH",
            "ema_pullback",
            entry=bar.close,
            stop=bar.close - sign * 3,
            target=bar.close + sign * 4.5,
        )


def test_original_signal_requires_and_accepts_zone_confluence() -> None:
    candles = fakeout_candles()
    candles[-2] = candle(98, 95.0, 96.0, 94.2, 95.5)
    candles[-1] = candle(99, 95.6, 97.2, 95.4, 97.0)
    long_strategy = strategy()
    long_strategy._original = (ScriptedOriginal("BUY"),)  # type: ignore[assignment]
    short_strategy = strategy()
    short_strategy._original = (ScriptedOriginal("SELL"),)  # type: ignore[assignment]

    long = long_strategy.analyze(candles)
    short = short_strategy.analyze(mirror(candles))

    assert long.direction == "BUY"
    assert short.direction == "SELL"
    assert long.strategy == short.strategy == "5m_evolution_confluence"


def test_unconfirmed_recent_extreme_is_not_a_zone() -> None:
    candles = fakeout_candles()
    candles[70] = candle(70, 100, 101, 99, 100)
    candles[97] = candle(97, 96, 97, 94, 96)

    assert not strategy().analyze(candles).ready


def test_live_strategy_name_and_requested_demo_risk_profile_parse() -> None:
    built = _strategies(("5m_evolution",), "MINUTE_5")
    args = _parser().parse_args(
        [
            "watch", "--session", "5m-evolution", "--resolution", "MINUTE_5",
            "--trend-resolution", "MINUTE_30", "--hours", "all", "--strategies",
            "5m_evolution", "--starting-balance", "11000", "--risk-per-trade", "0.02",
            "--max-daily-loss", "0.10", "--max-trades-per-day", "50", "--max-positions", "5",
            "--max-size", "100", "--cap-daily-risk", "--no-manage", "--record-path",
        ]
    )
    settings = _settings(args)

    assert isinstance(built[0], FiveMinuteEvolutionStrategy)
    assert settings.paper_starting_balance == 11_000
    assert settings.risk_per_trade == 0.02
    assert settings.max_daily_loss == 0.10
    assert settings.max_trades_per_day == 50
    assert settings.max_positions_per_session == 5
    assert settings.cap_daily_risk
    assert not settings.manage_trades
    assert settings.record_price_path


