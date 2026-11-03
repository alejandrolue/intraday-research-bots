"""Issue #94: Fibonacci retracement continuation on hourly GOLD (research only).

A leg is a confirmed swing low followed by a confirmed swing high (long) or the reverse (short),
at least 3 ATR long. Swings are pivots with 3 candles on each side and become usable only after the
3 later candles have closed. When a leg's end is confirmed and the 4-hour trend agrees, a limit
order is placed at the chosen Fibonacci level. It fills when price trades through it by the spread,
and it is cancelled when price passes the 78.6% level, makes a new extreme beyond the leg end, or
after 48 candles.

After a fill: hard stop beyond the swing start (0.1 ATR buffer); exit at the close of a candle
beyond the 78.6% level ("leg failed"); target per variant; time exit after 120 candles; financing
per calendar night. One position at a time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from goldbot.models import Candle
from goldbot.strategy import HigherTimeframeTrendFilter

RADIUS = 3
MIN_LEG_ATR = 3.0
ORDER_BARS = 48
MAX_HOLD = 120
FAIL = 0.786


@dataclass(frozen=True)
class Rule:
    level: float        # 0.382 | 0.5 | 0.618
    confluence: bool
    target: str         # "high" (leg end) | "ext" (1.272) | "2R"

    @property
    def id(self) -> str:
        return f"fib{self.level:g}-{'conf' if self.confluence else 'any'}-{self.target}"


RULES = tuple(Rule(level, conf, tgt) for level in (0.382, 0.5, 0.618) for conf in (False, True)
              for tgt in ("high", "ext", "2R"))


def atr(bars: list[Candle], t: int, period: int = 14) -> float:
    lo = max(1, t - period + 1)
    ranges = [max(bars[i].high - bars[i].low, abs(bars[i].high - bars[i - 1].close),
                  abs(bars[i].low - bars[i - 1].close)) for i in range(lo, t + 1)]
    return sum(ranges) / len(ranges)


def four_hour(bars: list[Candle]) -> list[Candle]:
    """4-hour candles (UTC 00/04/08/...) built from hourly candles."""
    groups: dict[datetime, list[Candle]] = {}
    for b in bars:
        key = b.timestamp.replace(hour=b.timestamp.hour - b.timestamp.hour % 4, minute=0)
        groups.setdefault(key, []).append(b)
    return [Candle(k, g[0].open, max(x.high for x in g), min(x.low for x in g), g[-1].close)
            for k, g in sorted(groups.items())]


def ema_series(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    value = sum(values[:period]) / period
    out[period - 1] = value
    k = 2 / (period + 1)
    for i in range(period, len(values)):
        value = (values[i] - value) * k + value
        out[i] = value
    return out


def trend_bias(bars: list[Candle]) -> list[str]:
    """4h bias available at the close of each hourly candle (only completed 4h candles)."""
    h4 = four_hour(bars)
    trend = HigherTimeframeTrendFilter()
    biases = []
    j = -1
    cache: dict[int, str] = {}
    for b in bars:
        close_time = b.timestamp + timedelta(hours=1)
        while j + 1 < len(h4) and h4[j + 1].timestamp + timedelta(hours=4) <= close_time:
            j += 1
        if j not in cache:
            done = h4[max(0, j - 149):j + 1]
            cache[j] = trend.analyze(done) if len(done) >= trend.minimum_candles else "NEUTRAL"
        biases.append(cache[j])
    return biases


def confluent(lo_price: float, hi_price: float, level_prices: list[float], ema50: float | None,
              tolerance: float) -> bool:
    """Something structural inside the 38.2-61.8% zone: a round $50, a prior swing, or EMA 50."""
    lo, hi = min(lo_price, hi_price) - tolerance, max(lo_price, hi_price) + tolerance
    first_round = int(lo // 50 + 1) * 50
    if first_round <= hi:
        return True
    if ema50 is not None and lo <= ema50 <= hi:
        return True
    return any(lo <= p <= hi for p in level_prices)


@dataclass(frozen=True)
class Trade:
    direction: int
    opened: date
    closed: date
    entry: float
    exit: float
    risk: float
    financing: float
    reason: str

    @property
    def r(self) -> float:
        return ((self.exit - self.entry) * self.direction - self.financing) / self.risk


def setups(bars: list[Candle]) -> list[dict]:
    """Every confirmed leg end with its levels (independent of the rule's entry choices)."""
    out = []
    lows: list[tuple[int, float]] = []
    highs: list[tuple[int, float]] = []
    for c in range(2 * RADIUS, len(bars)):
        k = c - RADIUS                                  # the pivot candidate, confirmed at c
        window = bars[k - RADIUS:k + RADIUS + 1]
        if bars[k].high == max(b.high for b in window) and bars[k].high > max(b.high for b in bars[k - RADIUS:k]):
            highs.append((k, bars[k].high))
            if lows and lows[-1][0] < k:
                lk, lp = lows[-1]
                out.append({"confirmed": c, "direction": 1, "start": lp, "end": bars[k].high,
                            "start_index": lk, "end_index": k})
        if bars[k].low == min(b.low for b in window) and bars[k].low < min(b.low for b in bars[k - RADIUS:k]):
            lows.append((k, bars[k].low))
            if highs and highs[-1][0] < k:
                hk, hp = highs[-1]
                out.append({"confirmed": c, "direction": -1, "start": hp, "end": bars[k].low,
                            "start_index": hk, "end_index": k})
    for s in out:
        prior = [p for i, p in highs + lows if i < s["start_index"] and i >= s["start_index"] - 200]
        s["structure"] = prior
    return out


def simulate(bars: list[Candle], rule: Rule, legs: list[dict], biases: list[str], ema50: list,
             start: date, end: date, *, financing_rate: float, spread_mult: float = 1.0) -> list[Trade]:
    trades: list[Trade] = []
    by_confirm: dict[int, list[dict]] = {}
    for leg in legs:
        by_confirm.setdefault(leg["confirmed"], []).append(leg)
    order: dict | None = None
    position: dict | None = None
    for t in range(1, len(bars)):
        b = bars[t]
        day = b.timestamp.date()
        half = b.spread * spread_mult / 2
        # 1. manage an open position
        if position:
            sign = position["direction"]
            lo, hi = (b.low - half, b.high - half) if sign == 1 else (b.low + half, b.high + half)
            exit_price, reason = None, ""
            if (sign == 1 and lo <= position["stop"]) or (sign == -1 and hi >= position["stop"]):
                gap = b.open - half if sign == 1 else b.open + half
                exit_price = min(position["stop"], gap) if sign == 1 else max(position["stop"], gap)
                reason = "stop"
            elif (sign == 1 and hi >= position["target"]) or (sign == -1 and lo <= position["target"]):
                exit_price, reason = position["target"], "target"
            elif (b.close - position["fail"]) * sign < 0:
                exit_price, reason = (b.close - half if sign == 1 else b.close + half), "failed"
            elif t - position["opened_index"] >= MAX_HOLD or day >= end:
                exit_price, reason = (b.close - half if sign == 1 else b.close + half), "time"
            if exit_price is not None:
                trades.append(Trade(sign, position["opened"], day, position["entry"], exit_price,
                                    position["risk"], position["financing"], reason))
                position = None
            elif t + 1 < len(bars):
                nights = (bars[t + 1].timestamp.date() - day).days
                position["financing"] += financing_rate / 365 * b.close * nights
        # 2. a pending order fills or is cancelled
        if order and not position:
            sign, level = order["direction"], order["level"]
            through = (b.low <= level - b.spread * spread_mult) if sign == 1 else (b.high >= level + b.spread * spread_mult)
            beyond_fail = (b.low <= order["fail"]) if sign == 1 else (b.high >= order["fail"])
            new_extreme = (b.high > order["end"]) if sign == 1 else (b.low < order["end"])
            if through and start <= day < end and t - order["placed"] <= ORDER_BARS:
                risk = abs(level - order["stop"])
                target = level + sign * 2 * risk if order["target_kind"] == "2R" else order["target"]
                position = {"direction": sign, "entry": level, "stop": order["stop"], "target": target,
                            "fail": order["fail"], "risk": risk, "opened": day, "opened_index": t,
                            "financing": 0.0}
                order = None
                # The rest of the fill candle still counts: a stop, then a close beyond 78.6%.
                stop_hit = (b.low - half <= position["stop"]) if sign == 1 else (b.high + half >= position["stop"])
                failed = (b.close - position["fail"]) * sign < 0
                if stop_hit or failed:
                    price = position["stop"] if stop_hit else (b.close - half if sign == 1 else b.close + half)
                    trades.append(Trade(sign, day, day, level, price, risk, 0.0, "stop" if stop_hit else "failed"))
                    position = None
            elif beyond_fail or new_extreme or t - order["placed"] > ORDER_BARS:
                order = None
        # 3. new legs confirmed at this close create an order (the newest leg wins)
        for leg in by_confirm.get(t, []):
            if position:
                break
            sign = leg["direction"]
            want = "BULLISH" if sign == 1 else "BEARISH"
            a = atr(bars, t)
            size = abs(leg["end"] - leg["start"])
            if biases[t] != want or a <= 0 or size < MIN_LEG_ATR * a:
                continue
            level = leg["end"] - sign * rule.level * size
            fail = leg["end"] - sign * FAIL * size
            if (b.close - level) * sign <= 0:
                continue  # price already beyond the entry level
            if rule.confluence:
                z1, z2 = leg["end"] - sign * 0.382 * size, leg["end"] - sign * 0.618 * size
                if not confluent(z1, z2, leg["structure"], ema50[t], 0.1 * a):
                    continue
            stop = leg["start"] - sign * 0.1 * a
            target = leg["end"] if rule.target == "high" else leg["end"] + sign * 0.272 * size
            order = {"direction": sign, "level": level, "fail": fail, "stop": stop, "end": leg["end"],
                     "target": target, "target_kind": rule.target, "placed": t}
    return trades
