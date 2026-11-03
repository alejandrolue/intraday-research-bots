"""Issue #83: hourly range (sideways) and squeeze-breakout rules, plus their simulator.

A signal uses candle t's completed data. The order fills at candle t+1's open (buy at open + half
spread, sell at open - half spread). Stop and target are checked from the fill candle on with the
closing side of the quote; if both fall in one candle the stop counts first; a gap through the stop
fills at the (worse) open. A trade still open after `max_bars` candles closes at the next open.
Financing is charged per calendar night held. One position per market at a time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from goldbot.models import Candle
from goldbot.regime import market_regime

LOOKBACK = 20


@dataclass(frozen=True)
class Rule:
    family: str          # "range" | "breakout"
    a: str               # range: "strict"|"loose" state;   breakout: box width "3"|"4" ATR
    b: str               # range: stop buffer "0.5"|"1.0"; breakout: stop "mid"|"opposite"
    c: str               # range: target "mid"|"edge";       breakout: target "2"|"3" R

    @property
    def id(self) -> str:
        return f"{self.family}-{self.a}-{self.b}-{self.c}"

    @property
    def max_bars(self) -> int:
        return 24 if self.family == "range" else 48


RULES = tuple(
    [Rule("range", a, b, c) for a in ("strict", "loose") for b in ("0.5", "1.0") for c in ("mid", "edge")]
    + [Rule("breakout", a, b, c) for a in ("3", "4") for b in ("mid", "opposite") for c in ("2", "3")]
)


@dataclass(frozen=True)
class Features:
    atr: float
    efficiency: float
    adx: float
    prior_high: float
    prior_low: float


def features(bars: list[Candle]) -> list[Features | None]:
    """Per-candle market state from completed candles only (the same function the bots log)."""
    out: list[Features | None] = []
    for t in range(len(bars)):
        if t < 300:
            out.append(None)
            continue
        state = market_regime(bars[t - 299:t + 1])
        if not state:
            out.append(None)
            continue
        prior = bars[t - LOOKBACK:t]
        out.append(Features(state["atr"], state["efficiency"], state["adx"],
                            max(b.high for b in prior), min(b.low for b in prior)))
    return out


def signal(rule: Rule, bar: Candle, f: Features | None) -> tuple[int, float, float] | None:
    """(direction, stop, target) for an order at the next open, or None."""
    if f is None or f.atr <= 0:
        return None
    atr, high, low = f.atr, f.prior_high, f.prior_low
    mid = (high + low) / 2
    if rule.family == "range":
        sideways = f.efficiency < 0.3 and f.adx < (20 if rule.a == "strict" else 25)
        if not sideways:
            return None
        buffer = float(rule.b) * atr
        if bar.low <= low + 0.1 * atr and low < bar.close < mid and bar.close > bar.open:
            stop = min(low, bar.low) - buffer
            target = mid if rule.c == "mid" else high - 0.25 * atr
            direction = 1
        elif bar.high >= high - 0.1 * atr and mid < bar.close < high and bar.close < bar.open:
            stop = max(high, bar.high) + buffer
            target = mid if rule.c == "mid" else low + 0.25 * atr
            direction = -1
        else:
            return None
        if abs(target - bar.close) < 0.8 * abs(bar.close - stop):
            return None
        return direction, stop, target
    if high - low > float(rule.a) * atr:
        return None
    body, span = abs(bar.close - bar.open), bar.high - bar.low
    if body < 0.5 * atr or span <= 0:
        return None
    if bar.close > high + 0.1 * atr and bar.close >= bar.high - 0.25 * span:
        direction = 1
    elif bar.close < low - 0.1 * atr and bar.close <= bar.low + 0.25 * span:
        direction = -1
    else:
        return None
    stop = mid if rule.b == "mid" else (low - 0.1 * atr if direction == 1 else high + 0.1 * atr)
    risk = abs(bar.close - stop)
    return direction, stop, bar.close + direction * float(rule.c) * risk


@dataclass(frozen=True)
class Trade:
    market: str
    direction: int
    opened: date
    closed: date
    entry: float
    exit: float
    risk: float
    financing_points: float
    reason: str

    @property
    def r(self) -> float:
        return ((self.exit - self.entry) * self.direction - self.financing_points) / self.risk


def simulate(market: str, bars: list[Candle], feats: list[Features | None], rule: Rule,
             start: date, end: date, *, financing_rate: float, spread_mult: float = 1.0) -> list[Trade]:
    trades: list[Trade] = []
    t = 0
    while t < len(bars) - 1:
        bar = bars[t]
        if bar.timestamp.date() >= end:
            break
        found = signal(rule, bar, feats[t])
        fill = bars[t + 1]
        if found is None or not start <= fill.timestamp.date() < end:
            t += 1
            continue
        direction, stop, target = found
        half = fill.spread * spread_mult / 2
        entry = fill.open + half if direction == 1 else fill.open - half
        risk = abs(entry - stop)
        if risk <= 0 or (target - entry) * direction <= 0:
            t += 1
            continue
        financing, exit_price, reason, k = 0.0, None, "", t + 1
        while k < len(bars):
            b = bars[k]
            h = b.spread * spread_mult / 2
            if k - (t + 1) >= rule.max_bars or b.timestamp.date() >= end:
                exit_price, reason = (b.open - h if direction == 1 else b.open + h), "time"
                break
            low, high = b.low - h if direction == 1 else b.low + h, b.high - h if direction == 1 else b.high + h
            if (direction == 1 and low <= stop) or (direction == -1 and high >= stop):
                gap = b.open - h if direction == 1 else b.open + h
                exit_price = min(stop, gap) if direction == 1 else max(stop, gap)
                reason = "stop"
                break
            if (direction == 1 and high >= target) or (direction == -1 and low <= target):
                exit_price, reason = target, "target"
                break
            if k + 1 < len(bars):
                nights = (bars[k + 1].timestamp.date() - b.timestamp.date()).days
                financing += financing_rate / 365 * b.close * nights
            k += 1
        if exit_price is None:
            break
        trades.append(Trade(market, direction, fill.timestamp.date(), bars[min(k, len(bars) - 1)].timestamp.date(),
                            entry, exit_price, risk, financing, reason))
        t = k  # the next signal can come from the exit candle on
    return trades
