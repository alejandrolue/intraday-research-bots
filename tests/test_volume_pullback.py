from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from goldbot.cli import _parser, _settings, _strategies
from goldbot.market_events import minutes_after_open, nearest_open, news_blackout
from goldbot.models import Candle
from goldbot.volume_pullback import (
    Ema21TrendFilter, VolumePullbackConfig, VolumePullbackStrategy, _engulfing, _mirror, _pin_bar,
)

U = timezone.utc


def _walk(n: int, seed: int, step: timedelta = timedelta(minutes=5)) -> list[Candle]:
    rng = random.Random(seed)
    price, out = 4000.0, []
    start = datetime(2026, 3, 2, tzinfo=U)
    for i in range(n):
        drift = 0.6 * (1 if (i // 120) % 2 == 0 else -1)
        opened = price
        price += drift + rng.gauss(0, 1.5)
        high = max(opened, price) + abs(rng.gauss(0, 0.6))
        low = min(opened, price) - abs(rng.gauss(0, 0.6))
        out.append(Candle(start + i * step, opened, high, low, price,
                          volume=max(1.0, rng.gauss(1000, 400)), spread=0.3))
    return out


def test_names_parse_and_build() -> None:
    config = VolumePullbackConfig.parse("evp-combined-rvol-rr3")
    assert (config.entry, config.volume, config.reward_to_risk) == ("combined", "rvol", 3.0)
    assert isinstance(_strategies(("evp-pullback-sma-rr2",))[0], VolumePullbackStrategy)
    with pytest.raises(ValueError):
        VolumePullbackConfig.parse("evp-pullback-tick-rr2")


def test_session_options_for_the_live_profile() -> None:
    args = _parser().parse_args(["watch", "--session", "5m-v1", "--trend-filter", "ema21_spec",
                                 "--price-count", "1000", "--strategies", "evp-pullback-sma-rr2"])
    settings = _settings(args)
    assert settings.trend_filter == "ema21_spec" and settings.capital_price_count == 1000
    with pytest.raises(ValueError):
        _settings(_parser().parse_args(["watch", "--price-count", "5000"]))


def test_engulfing_and_pin_bar() -> None:
    t = datetime(2026, 1, 5, tzinfo=U)
    red = Candle(t, 10.0, 10.2, 9.0, 9.2)
    assert _engulfing(red, Candle(t, 9.1, 10.4, 9.0, 10.3))
    assert not _engulfing(red, Candle(t, 9.3, 9.9, 9.2, 9.8))
    assert _pin_bar(Candle(t, 10.0, 10.2, 8.0, 10.1))
    assert not _pin_bar(Candle(t, 10.0, 10.2, 8.0, 8.5))


def test_long_and_short_rules_are_mirror_images() -> None:
    hits = 0
    for entry in ("pullback", "retest", "breakout", "combined"):
        candles = _walk(1300, seed=7)
        mirrored = [_mirror(candle) for candle in candles]
        a = VolumePullbackStrategy(VolumePullbackConfig(entry=entry, volume="off"))
        b = VolumePullbackStrategy(VolumePullbackConfig(entry=entry, volume="off"))
        for end in range(1000, len(candles)):
            left, right = a.analyze(candles[end - 999:end + 1]), b.analyze(mirrored[end - 999:end + 1])
            assert left.ready == right.ready
            if left.ready:
                hits += 1
                assert {left.decision, right.decision} == {"READY_LONG", "READY_SHORT"}
                assert left.entry == pytest.approx(-right.entry)
                assert left.stop == pytest.approx(-right.stop)
                assert left.target == pytest.approx(-right.target)
                assert (left.target - left.entry) == pytest.approx(
                    2.0 * (left.entry - left.stop), rel=1e-9)
    assert hits > 0


def test_reopen_guard_blocks_the_first_fifteen_minutes() -> None:
    candles = _walk(1000, seed=3)
    gap = timedelta(hours=1)
    shifted = candles[:-2] + [Candle(c.timestamp + gap, c.open, c.high, c.low, c.close, c.volume, c.spread)
                              for c in candles[-2:]]
    strategy = VolumePullbackStrategy()
    assert strategy.analyze(shifted).reason == "REOPEN_GUARD"
    assert strategy.analyze(shifted[:-1]).reason == "REOPEN_GUARD"


def test_news_blackout_window_is_dst_aware() -> None:
    u = lambda *a: datetime(*a, tzinfo=U)
    assert news_blackout(u(2026, 10, 2, 12, 15)) == "NFP"           # 08:15 EDT
    assert news_blackout(u(2026, 10, 2, 13, 0)) is None             # 09:00 EDT, window over
    assert news_blackout(u(2026, 11, 6, 13, 15)) == "NFP"           # 08:15 EST after the US change
    assert news_blackout(u(2026, 11, 6, 12, 30)) is None
    assert news_blackout(u(2026, 10, 28, 18, 59)) == "FOMC"         # until 30 min after 14:30 EDT
    assert news_blackout(u(2026, 11, 3, 5, 0)) == "US elections"
    assert news_blackout(u(2026, 11, 4, 5, 0)) is None


def test_market_opens_follow_both_dst_changes() -> None:
    u = lambda *a: datetime(*a, tzinfo=U)
    assert nearest_open(u(2026, 10, 1, 7, 10)) == ("london", 10)    # 08:10 BST
    assert nearest_open(u(2026, 10, 26, 8, 5)) == ("london", 5)     # 08:05 GMT
    assert nearest_open(u(2026, 11, 2, 14, 35)) == ("ny-cash", 5)   # 09:35 EST
    assert minutes_after_open(u(2026, 10, 1, 13, 40)) == 10
    assert minutes_after_open(u(2026, 10, 1, 11, 0)) is None


def test_trend_filter_bias_and_ema200_band() -> None:
    up = _walk(600, seed=1, step=timedelta(minutes=30))
    flat = [Candle(c.timestamp, 4000, 4000.5, 3999.5, 4000) for c in up]
    assert Ema21TrendFilter().analyze(flat) == "NEUTRAL"
    rising = [Candle(c.timestamp, 4000 + i, 4000.5 + i, 3999.5 + i, 4000.4 + i)
              for i, c in enumerate(up)]
    assert Ema21TrendFilter().analyze(rising) == "BULLISH"
    falling = [_mirror(c) for c in rising]
    assert Ema21TrendFilter().analyze(falling) == "BEARISH"
