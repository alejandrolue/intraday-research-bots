"""Session opening-range strategies from the issue #12 research, for live forward testing.

This is a copy of `research/five_minute_strategies.py` (whose source is hash-locked by the
research runner and must not change). `tests/test_session_strategies.py` checks that both
produce identical signals. Strategy names are the research variant IDs, e.g.
`orb-nycash-15-rr2`; the trend filter is configured separately
(`--trend-resolution`), as in the research.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import Candle, MarketSetup


_ID = re.compile(
    r"^(orb|rolling_sweep|orbside|orbmid)-(london|ny|nycash|tokyo|hongkong)-(5|15|30|60)"
    r"-rr(\d+(?:\.\d+)?)$"
)

# Session opens in local time. "ny" is the 08:30 ET US data / COMEX open used for gold;
# "nycash" is the 09:30 ET stock-market cash open used for US indices. "tokyo" (09:00 JST) and
# "hongkong" (09:30 HKT) are the Asian cash opens; neither zone observes daylight saving.
SESSIONS = {
    "london": ("Europe/London", 8, 0),
    "ny": ("America/New_York", 8, 30),
    "nycash": ("America/New_York", 9, 30),
    "tokyo": ("Asia/Tokyo", 9, 0),
    "hongkong": ("Asia/Hong_Kong", 9, 30),
}


@dataclass(frozen=True)
class SessionVariant:
    family: str
    session: str
    minutes: int
    reward: float

    @property
    def id(self) -> str:
        return f"{self.family}-{self.session}-{self.minutes}-rr{self.reward:g}"

    @classmethod
    def parse(cls, name: str) -> "SessionVariant":
        match = _ID.match(name)
        if not match:
            raise ValueError(
                f"Unknown session strategy {name!r}; expected e.g. orb-nycash-15-rr2"
            )
        family, session, minutes, reward = match.groups()
        return cls(family, session, int(minutes), float(reward))


class SessionStrategy:
    minimum_candles = 16

    def __init__(
        self,
        variant: SessionVariant,
        *,
        start: datetime | None = None,
        bar_minutes: int = 5,
    ) -> None:
        if variant.minutes % bar_minutes:
            raise ValueError(f"{variant.id}: range length must be a multiple of the candle size")
        self.variant = variant
        self.bar_minutes = bar_minutes
        self.name = variant.id
        self.start = start
        zone, self.open_hour, self.open_minute = SESSIONS[variant.session]
        self.zone = ZoneInfo(zone)

    def analyze(self, candles: list[Candle]) -> MarketSetup:
        bar = candles[-1]
        waiting = MarketSetup("NO_TRADE", bar.timestamp, "No session trigger", "NEUTRAL", self.name)
        if len(candles) < self.minimum_candles or (self.start and bar.timestamp < self.start):
            return waiting
        local = bar.timestamp.astimezone(self.zone)
        opened = local.replace(hour=self.open_hour, minute=self.open_minute,
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
        step = self.bar_minutes
        count = self.variant.minutes // step
        if self.variant.family == "rolling_sweep":
            ranged = candles[-count - 1:-1]
            expected = [bar.timestamp - timedelta(minutes=step * i) for i in range(count, 0, -1)]
        else:
            end = opened + timedelta(minutes=self.variant.minutes)
            ranged = [c for c in candles[-(count + 200):] if opened <= c.timestamp.astimezone(self.zone) < end]
            expected = [opened + timedelta(minutes=step * i) for i in range(count)]
        if [c.timestamp for c in ranged] != expected:
            return waiting
        high, low = max(c.high for c in ranged), min(c.low for c in ranged)
        buffer = 0.1 * atr
        if self.variant.family in ("orbside", "orbmid"):
            # Article rules (issue #31): enter on the first close beyond the range, stop at the
            # opposite side of the range (orbside) or at its midpoint (orbmid), fixed R target.
            long = candles[-2].close <= high < bar.close
            short = candles[-2].close >= low > bar.close
            stop_level = (high + low) / 2 if self.variant.family == "orbmid" else (low if long else high)
            distance = abs(bar.close - stop_level)
            if distance <= 0:
                return waiting
        elif self.variant.family == "orb":
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
