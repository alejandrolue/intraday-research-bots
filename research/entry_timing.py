"""Issue #92: 5m-learn signals executed on 1-minute candles with different entries and exits."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from goldbot.models import Candle
from goldbot.risk import RiskConfig
from goldbot.runner import BotRunner
from goldbot.strategy import DemandZoneStrategy, HigherTimeframeTrendFilter, PullbackStrategy, RetestStrategy

WINDOW = 499
FIVE, ONE = timedelta(minutes=5), timedelta(minutes=1)


@dataclass(frozen=True)
class Signal:
    time: datetime          # 5m close (the moment the live bot acts)
    direction: int
    close: float
    stop: float
    target: float
    high: float
    low: float
    atr: float
    strategy: str


def signals(m5: list[Candle], m30: list[Candle], start: datetime, end: datetime) -> list[Signal]:
    """The live 5m-learn signal stream (strategies, selection, 30m trend, reward and spread gates)."""
    strategies = [RetestStrategy(), PullbackStrategy(), DemandZoneStrategy()]
    trend = HigherTimeframeTrendFilter()
    gate = RiskConfig()
    out, t30 = [], 0
    for i in range(WINDOW, len(m5)):
        bar = m5[i]
        close_time = bar.timestamp + FIVE
        if not start <= bar.timestamp < end:
            continue
        window = m5[i - WINDOW + 1:i + 1]
        setup = BotRunner._select_setup(tuple(s.analyze(window) for s in strategies))
        if not setup.ready:
            continue
        while t30 + 1 < len(m30) and m30[t30 + 1].timestamp + timedelta(minutes=30) <= close_time:
            t30 += 1
        completed = m30[max(0, t30 - WINDOW + 1):t30 + 1]
        bias = trend.analyze(completed) if len(completed) >= trend.minimum_candles else "NEUTRAL"
        if not BotRunner._apply_trend_filter(setup, bias).ready:
            continue
        risk = abs(setup.entry - setup.stop)
        if risk <= 0 or abs(setup.target - setup.entry) / risk < gate.minimum_reward_to_risk - 1e-9:
            continue
        if bar.spread > gate.max_spread:
            continue
        out.append(Signal(close_time, 1 if setup.direction == "BUY" else -1, bar.close, setup.stop,
                          setup.target, bar.high, bar.low, setup.atr or risk, setup.strategy))
    return out


@dataclass
class _Order:
    sig: Signal
    kind: str
    expires: datetime
    pulled: bool = False
    fire_next: bool = False
    prev: Candle | None = None


@dataclass
class _Position:
    sig: Signal
    entry: float
    target: float
    stop: float
    opened: datetime


@dataclass
class Trade:
    signal_time: datetime
    direction: int
    r: float
    reason: str


@dataclass
class Variant:
    entry: str      # market | L50 | M1
    cooldown: bool
    extend: bool

    @property
    def id(self) -> str:
        return f"{self.entry}-{'cool' if self.cooldown else 'nocool'}-{'extend' if self.extend else 'fixed'}"


def execute(m1: list[Candle], sigs: list[Signal], v: Variant) -> list[Trade]:
    trades: list[Trade] = []
    by_time: dict[datetime, list[Signal]] = {}
    for s in sigs:
        by_time.setdefault(s.time, []).append(s)
    orders: list[_Order] = []
    positions: list[_Position] = []
    day, day_count = None, 0
    streak = {1: 0, -1: 0}
    blocked = {1: datetime.min.replace(tzinfo=timezone.utc), -1: datetime.min.replace(tzinfo=timezone.utc)}
    pending_sigs: list[Signal] = []

    def close(p: _Position, price: float, t: datetime, reason: str) -> None:
        s = p.sig
        r = (price - p.entry) * s.direction / abs(p.entry - s.stop)
        trades.append(Trade(s.time, s.direction, r, reason))
        positions.remove(p)
        if v.cooldown:
            if reason == "stop":
                streak[s.direction] += 1
                if streak[s.direction] >= 2:
                    blocked[s.direction] = t + timedelta(minutes=120)
                    streak[s.direction] = 0
            elif r > 0:
                streak[s.direction] = 0

    def open_at(s: Signal, price: float, t: datetime) -> None:
        nonlocal day_count
        if (price - s.stop) * s.direction <= 0 or (s.target - price) * s.direction <= 0:
            return
        positions.append(_Position(s, price, s.target, s.stop, t))
        day_count += 1

    for b in m1:
        t = b.timestamp
        half = b.spread / 2
        if day != t.date():
            day, day_count = t.date(), 0
        friday_close = t.weekday() == 4 and (t.hour, t.minute) >= (20, 45)
        if friday_close:
            for p in list(positions):
                close(p, b.open - half if p.sig.direction == 1 else b.open + half, t, "weekend")
            orders.clear()
            pending_sigs.clear()
        # signals whose 5m close falls inside this minute (or was missed during a gap) become orders
        pending_sigs.extend(by_time.get(t, []))
        while pending_sigs and not friday_close and t.weekday() != 5:
            s = pending_sigs.pop(0)
            if t - s.time > ONE * 5:
                continue
            if t < blocked[s.direction]:
                continue
            busy = [p.sig.direction for p in positions] + [o.sig.direction for o in orders]
            if (busy and busy[0] != s.direction) or len(busy) >= 3 or day_count >= 50:
                continue
            if v.entry == "market":
                price = b.open + half if s.direction == 1 else b.open - half
                if abs(price - s.close) <= 0.5 * abs(s.close - s.stop):
                    open_at(s, price, t)
            else:
                orders.append(_Order(s, v.entry, t + timedelta(minutes=15)))
        # pending orders
        for o in list(orders):
            s, sign = o.sig, o.sig.direction
            if o.fire_next:
                orders.remove(o)
                price = b.open + half if sign == 1 else b.open - half
                if abs(price - s.close) <= 0.5 * abs(s.close - s.stop):
                    open_at(s, price, t)
                continue
            if t >= o.expires:
                orders.remove(o)
                continue
            stop_traded = (b.low - half <= s.stop) if sign == 1 else (b.high + half >= s.stop)
            if stop_traded:
                orders.remove(o)
                continue
            if o.kind == "L50":
                limit = s.close - sign * 0.5 * (s.high - s.low)
                through = (b.low <= limit - b.spread) if sign == 1 else (b.high >= limit + b.spread)
                if through:
                    orders.remove(o)
                    open_at(s, limit, t + ONE)  # exits are checked from the next minute
            else:  # M1
                if not o.pulled and ((s.close - b.low) if sign == 1 else (b.high - s.close)) >= 0.3 * s.atr:
                    o.pulled = True
                    o.prev = b
                    continue
                if o.pulled:
                    prev = getattr(o, "prev", None)
                    if prev is not None and ((sign == 1 and b.close > prev.high) or (sign == -1 and b.close < prev.low)):
                        o.fire_next = True
                    o.prev = b
        # exits for positions opened before this minute
        for p in list(positions):
            if p.opened >= t + ONE:
                continue
            s, sign = p.sig, p.sig.direction
            lo, hi = (b.low - half, b.high - half) if sign == 1 else (b.low + half, b.high + half)
            if (sign == 1 and lo <= p.stop) or (sign == -1 and hi >= p.stop):
                gap = b.open - half if sign == 1 else b.open + half
                reason = "stop" if p.stop == s.stop else "locked"
                close(p, min(p.stop, gap) if sign == 1 else max(p.stop, gap), t, reason)
                continue
            if (sign == 1 and hi >= p.target) or (sign == -1 and lo <= p.target):
                if not v.extend:
                    close(p, p.target, t, "target")
                    continue
            if v.extend:
                risk = abs(p.entry - s.stop)
                near = (hi >= p.target - 0.25 * risk) if sign == 1 else (lo <= p.target + 0.25 * risk)
                if near:
                    old = p.target
                    p.target = old + sign * risk
                    p.stop = old - sign * risk
    return trades
