"""Issue #75: daily trend-following simulator and rules (research only).

A signal is computed from day t's completed candle; the order fills at day t+1's open, buying at
open + half spread and selling at open - half spread. The initial stop is k x ATR(20) from the
fill and is checked from the fill day on with the closing side of the quote; a gap through the
stop fills at the (worse) open. Financing is charged for every calendar night a position is held,
at an annual rate on the position's value. R = net points / initial stop distance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from goldbot.models import Candle


@dataclass(frozen=True)
class Rule:
    family: str            # "donchian" | "ma" | "tsmom"
    a: int                 # entry channel / fast MA / momentum lookback
    b: int                 # exit channel / slow MA / unused
    stop_atr: float

    @property
    def id(self) -> str:
        if self.family == "donchian":
            return f"donchian-{self.a}-{self.b}-k{self.stop_atr:g}"
        if self.family == "ma":
            return f"ma-{self.a}-{self.b}-k{self.stop_atr:g}"
        return f"tsmom-{self.a}-k{self.stop_atr:g}"


RULES = (
    Rule("donchian", 20, 10, 2.0), Rule("donchian", 20, 10, 3.0),
    Rule("donchian", 55, 20, 2.0), Rule("donchian", 55, 20, 3.0),
    Rule("donchian", 100, 50, 2.0), Rule("donchian", 100, 50, 3.0),
    Rule("ma", 50, 200, 3.0), Rule("ma", 20, 100, 3.0),
    Rule("tsmom", 126, 0, 3.0), Rule("tsmom", 252, 0, 3.0),
)


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


def _atr(bars: list[Candle], end: int, period: int = 20) -> float | None:
    if end < period:
        return None
    ranges = [max(bars[i].high - bars[i].low, abs(bars[i].high - bars[i - 1].close),
                  abs(bars[i].low - bars[i - 1].close)) for i in range(end - period + 1, end + 1)]
    return sum(ranges) / period


def _sma(bars: list[Candle], end: int, period: int) -> float | None:
    if end + 1 < period:
        return None
    return sum(bar.close for bar in bars[end - period + 1:end + 1]) / period


def target(rule: Rule, bars: list[Candle], t: int, position: int) -> int | None:
    """Desired position after day t's close (+1, -1, 0), or None to keep the current one."""
    if rule.family == "donchian":
        if t < rule.a:
            return None
        close = bars[t].close
        entry_high = max(bar.high for bar in bars[t - rule.a:t])
        entry_low = min(bar.low for bar in bars[t - rule.a:t])
        exit_high = max(bar.high for bar in bars[t - rule.b:t])
        exit_low = min(bar.low for bar in bars[t - rule.b:t])
        if position == 1 and close < exit_low:
            return -1 if close < entry_low else 0
        if position == -1 and close > exit_high:
            return 1 if close > entry_high else 0
        if position == 0:
            if close > entry_high:
                return 1
            if close < entry_low:
                return -1
        return None
    if rule.family == "ma":
        fast, slow = _sma(bars, t, rule.a), _sma(bars, t, rule.b)
        if fast is None or slow is None or fast == slow:
            return None
        return 1 if fast > slow else -1
    if t < rule.a:
        return None
    change = bars[t].close - bars[t - rule.a].close
    return None if change == 0 else (1 if change > 0 else -1)


def simulate(market: str, bars: list[Candle], rule: Rule, start: date, end: date, *,
             financing_rate: float, spread_mult: float = 1.0, atr_period: int = 20) -> list[Trade]:
    """Trades opened in [start, end); a position still open at `end` closes at that day's open."""
    trades: list[Trade] = []
    position, entry, stop, risk, opened, financing = 0, 0.0, 0.0, 0.0, None, 0.0
    last_flip_blocked = 0  # after a stop-out, an always-in rule waits for the next flip
    pending: int | None = None
    for t in range(1, len(bars)):
        bar = bars[t]
        day = bar.timestamp.date()
        half = bar.spread * spread_mult / 2
        # 1) Orders decided at yesterday's close fill at today's open.
        if pending is not None and pending != position:
            if position != 0:
                exit_price = bar.open - half if position == 1 else bar.open + half
                trades.append(Trade(market, position, opened, day, entry, exit_price, risk,
                                    financing, "signal"))
                position = 0
            if pending != 0 and start <= day < end:
                atr = _atr(bars, t - 1, atr_period)
                if atr:
                    position = pending
                    entry = bar.open + half if position == 1 else bar.open - half
                    risk = rule.stop_atr * atr
                    stop = entry - position * risk
                    opened, financing = day, 0.0
        pending = None
        if position != 0 and day >= end:
            exit_price = bar.open - half if position == 1 else bar.open + half
            trades.append(Trade(market, position, opened, day, entry, exit_price, risk, financing, "end"))
            position = 0
        # 2) Stop during the day (closing side of the quote; a gap fills at the open).
        if position == 1 and bar.low - half <= stop:
            fill = min(stop, bar.open - half)
            trades.append(Trade(market, 1, opened, day, entry, fill, risk, financing, "stop"))
            position, last_flip_blocked = 0, 1
        elif position == -1 and bar.high + half >= stop:
            fill = max(stop, bar.open + half)
            trades.append(Trade(market, -1, opened, day, entry, fill, risk, financing, "stop"))
            position, last_flip_blocked = 0, -1
        # 3) Financing for each calendar night until the next candle.
        if position != 0 and t + 1 < len(bars):
            nights = (bars[t + 1].timestamp.date() - day).days
            financing += financing_rate / 365 * bar.close * nights
        # 4) Signal at today's close.
        if day >= end:
            break
        wanted = target(rule, bars, t, position)
        if wanted is None:
            continue
        if rule.family != "donchian" and position == 0 and wanted == last_flip_blocked:
            continue  # stopped out: wait for the trend to flip before trading this rule again
        if rule.family != "donchian" and wanted != last_flip_blocked:
            last_flip_blocked = 0
        if wanted != position:
            pending = wanted
    return trades
