from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from goldbot.models import Candle
from goldbot.regime import market_regime
from goldbot.telegram import _market_line

START = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)


def series(prices: list[float]) -> list[Candle]:
    out, previous = [], prices[0]
    for i, price in enumerate(prices):
        out.append(Candle(START + timedelta(minutes=5 * i), previous, max(previous, price) + 0.2,
                          min(previous, price) - 0.2, price))
        previous = price
    return out


def test_steady_rise_is_a_trend() -> None:
    state = market_regime(series([100 + 0.5 * i for i in range(150)]))
    assert state["label"] == "trend_up" and state["efficiency"] > 0.9


def test_choppy_range_is_sideways() -> None:
    state = market_regime(series([100 + 2 * math.sin(i * 1.3) for i in range(150)]))
    assert state["label"] == "sideways"
    assert 0.0 <= state["position_in_range"] <= 1.0


def test_close_above_a_narrow_range_is_a_breakout() -> None:
    prices = [100 + 0.3 * math.sin(i * 1.7) for i in range(149)] + [103.0]
    assert market_regime(series(prices))["label"] == "breakout_up"


def test_too_little_history_gives_no_state() -> None:
    assert market_regime(series([100.0] * 30)) == {}


def test_telegram_line() -> None:
    line = _market_line({"context": {"label": "sideways", "efficiency": 0.21, "adx": 16.8,
                                     "position_in_range": 0.85}})
    assert line == "Market: sideways (ER 0.21, ADX 17, at 85% of the 20-candle range)"
    assert _market_line({"context": {}}) is None
