from __future__ import annotations

from collections.abc import Sequence

from .models import Candle


def ema(values: Sequence[float], period: int) -> float:
    if period <= 0:
        raise ValueError("EMA period must be positive")
    if len(values) < period:
        raise ValueError(f"EMA({period}) requires at least {period} values")

    seed = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    result = seed
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def atr(candles: Sequence[Candle], period: int) -> float:
    if period <= 0:
        raise ValueError("ATR period must be positive")
    if len(candles) < period + 1:
        raise ValueError(f"ATR({period}) requires at least {period + 1} candles")

    true_ranges: list[float] = []
    for previous, current in zip(candles, candles[1:]):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return sum(true_ranges[-period:]) / period



def adx(candles: Sequence[Candle], period: int = 14) -> float:
    """Wilder's Average Directional Index: trend strength from 0 (none) to 100."""
    if period <= 0:
        raise ValueError("ADX period must be positive")
    if len(candles) < 2 * period + 1:
        raise ValueError(f"ADX({period}) requires at least {2 * period + 1} candles")

    true_ranges: list[float] = []
    plus_moves: list[float] = []
    minus_moves: list[float] = []
    for previous, current in zip(candles, candles[1:]):
        up = current.high - previous.high
        down = previous.low - current.low
        plus_moves.append(up if up > down and up > 0 else 0.0)
        minus_moves.append(down if down > up and down > 0 else 0.0)
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )

    tr = sum(true_ranges[:period])
    plus = sum(plus_moves[:period])
    minus = sum(minus_moves[:period])
    dx_values: list[float] = []
    for index in range(period, len(true_ranges) + 1):
        if index > period:
            tr = tr - tr / period + true_ranges[index - 1]
            plus = plus - plus / period + plus_moves[index - 1]
            minus = minus - minus / period + minus_moves[index - 1]
        if tr <= 0:
            dx_values.append(0.0)
            continue
        plus_di = 100 * plus / tr
        minus_di = 100 * minus / tr
        total = plus_di + minus_di
        dx_values.append(100 * abs(plus_di - minus_di) / total if total else 0.0)

    result = sum(dx_values[:period]) / period
    for value in dx_values[period:]:
        result = (result * (period - 1) + value) / period
    return result
