"""Issue #77: describe the market state at each decision (logging only, no trading effect).

Raw numbers are logged so later studies can choose their own thresholds; the label uses fixed,
pre-set thresholds and is only a convenience:

- breakout_up / breakout_down: the close leaves the previous 20-candle range, and that range was
  narrow (at most 4 ATR);
- trend_up / trend_down: efficiency ratio >= 0.5 or ADX >= 25, direction from the 20-candle move;
- sideways: efficiency ratio < 0.3 and ADX < 20;
- mixed: everything else.
"""

from __future__ import annotations

from typing import Any

from .indicators import adx, atr
from .market_events import nearest_open
from .models import Candle

LOOKBACK = 20


def _true_ranges(candles: list[Candle]) -> list[float]:
    return [
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        for p, c in zip(candles, candles[1:])
    ]


def market_regime(candles: list[Candle]) -> dict[str, Any]:
    """The state after the last completed candle; empty when there is too little history."""
    if len(candles) < 60:
        return {}
    last = candles[-1]
    closes = [c.close for c in candles[-(LOOKBACK + 1):]]
    path = sum(abs(b - a) for a, b in zip(closes, closes[1:]))
    efficiency = abs(closes[-1] - closes[0]) / path if path else 0.0
    current_atr = atr(candles, 14)
    if current_atr <= 0:
        return {}
    window = candles[-LOOKBACK:]
    high, low = max(c.high for c in window), min(c.low for c in window)
    prior = candles[-LOOKBACK - 1:-1]
    prior_high, prior_low = max(c.high for c in prior), min(c.low for c in prior)
    prior_range_atr = (prior_high - prior_low) / current_atr
    ranges = _true_ranges(candles[-301:])
    rolling = [sum(ranges[i - 14:i]) / 14 for i in range(14, len(ranges) + 1)]
    volatility_pct = sum(value <= current_atr for value in rolling) / len(rolling) if rolling else 0.5
    strength = adx(candles[-100:], 14)
    move_up = closes[-1] >= closes[0]
    if last.close > prior_high and prior_range_atr <= 4:
        label = "breakout_up"
    elif last.close < prior_low and prior_range_atr <= 4:
        label = "breakout_down"
    elif efficiency >= 0.5 or strength >= 25:
        label = "trend_up" if move_up else "trend_down"
    elif efficiency < 0.3 and strength < 20:
        label = "sideways"
    else:
        label = "mixed"
    opened, minutes = nearest_open(last.timestamp)
    return {
        "label": label,
        "efficiency": round(efficiency, 3),
        "adx": round(strength, 1),
        "range_atr": round((high - low) / current_atr, 2),
        "position_in_range": round((last.close - low) / (high - low), 2) if high > low else 0.5,
        "volatility_pct": round(volatility_pct, 2),
        "atr": round(current_atr, 4),
        "nearest_open": opened,
        "minutes_from_open": minutes,
    }
