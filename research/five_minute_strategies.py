"""Symmetric session hypotheses used only by the issue #12 research runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from itertools import product
from zoneinfo import ZoneInfo

from goldbot.models import Candle, MarketSetup


@dataclass(frozen=True)
class Variant:
    family: str
    session: str
    minutes: int
    reward: float
    trend: str | None

    @property
    def id(self) -> str:
        return f"{self.family}-{self.session}-{self.minutes}-rr{self.reward:g}-{self.trend or 'none'}"


def variants() -> list[Variant]:
    return [
        Variant(*values)
        for values in product(
            ("orb", "rolling_sweep"), ("london", "ny"),
            (30, 60), (1.6, 2.0), (None, "HOUR_4"),
        )
    ]


class SessionStrategy:
    minimum_candles = 16

    def __init__(self, variant: Variant, *, start=None) -> None:
        self.variant = variant
        self.name = variant.id
        self.start = start
        self.zone = ZoneInfo("Europe/London" if variant.session == "london" else "America/New_York")

    def analyze(self, candles: list[Candle]) -> MarketSetup:
        bar = candles[-1]
        waiting = MarketSetup("NO_TRADE", bar.timestamp, "No session trigger", "NEUTRAL", self.name)
        if len(candles) < self.minimum_candles or (self.start and bar.timestamp < self.start):
            return waiting
        local = bar.timestamp.astimezone(self.zone)
        opened = local.replace(hour=8, minute=0 if self.variant.session == "london" else 30,
                               second=0, microsecond=0)
        minutes = (local - opened).total_seconds() / 60
        if local.weekday() >= 5 or not self.variant.minutes <= minutes < 175:
            return waiting
        # Indicators and range boundaries contain no prices after the signal's close.
        previous = candles[-16:-1]
        atr = sum(
            max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
            for a, b in zip(previous, previous[1:])
        ) / 14
        if atr <= 0:
            return waiting
        count = self.variant.minutes // 5
        if self.variant.family == "rolling_sweep":
            ranged = candles[-count - 1:-1]
            expected = [bar.timestamp - timedelta(minutes=5 * i) for i in range(count, 0, -1)]
        else:
            end = opened + timedelta(minutes=self.variant.minutes)
            ranged = [c for c in candles if opened <= c.timestamp.astimezone(self.zone) < end]
            expected = [opened + timedelta(minutes=5 * i) for i in range(count)]
        if [c.timestamp for c in ranged] != expected:
            return waiting
        high, low = max(c.high for c in ranged), min(c.low for c in ranged)
        buffer = 0.1 * atr
        if self.variant.family == "orb":
            long = candles[-2].close <= high + buffer < bar.close
            short = candles[-2].close >= low - buffer > bar.close
            distance = max((high - low) / 2, 1.5 * atr)
        else:
            upper, lower = bar.high > high + buffer, bar.low < low - buffer
            if upper and lower:
                return waiting
            long = lower and low < bar.close < high and bar.close > bar.open
            short = upper and low < bar.close < high and bar.close < bar.open
            distance = max(bar.close - bar.low + buffer if long else bar.high - bar.close + buffer,
                           1.5 * atr)
        if long == short:
            return waiting
        sign = 1 if long else -1
        return MarketSetup(
            "READY_LONG" if long else "READY_SHORT", bar.timestamp, self.variant.family,
            "BULLISH" if long else "BEARISH", self.name, entry=bar.close,
            stop=bar.close - sign * distance,
            target=bar.close + sign * distance * self.variant.reward,
            atr=atr, zone_low=low, zone_high=high,
        )
