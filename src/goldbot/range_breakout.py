"""Issue #100: session range breakout (the exam pass `gold-london-asia-breakout`).

Each day the high/low of a range of hours (local time, DST-aware) is marked. The first candle inside the
entry window whose high or low breaks it by one spread (the candle's own spread) gives the signal; a
candle that breaks both sides, or any later break, gives none that day. The stop is the other side of
the range. There is no real target: positions close at the session's daily flat time (`--flat-at`), so
the target is set far away (10R) only to satisfy the reward-to-risk gate.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .models import Candle, MarketSetup

# name -> (time zone, range start, range end = window start, window end), local (hour, minute)
PRESETS = {
    "london": ("Europe/London", (0, 0), (7, 0), (12, 0)),      # #100: Asia range broken in London
}
_ID = re.compile(r"^rangebreak-(" + "|".join(PRESETS) + r")$")
MINIMUM_RANGE_CANDLES = 6
FAR_TARGET_R = 10.0


class RangeBreakoutStrategy:
    minimum_candles = 16

    def __init__(self, name: str) -> None:
        match = _ID.match(name)
        if not match:
            raise ValueError(f"Unknown range breakout strategy {name!r}; expected e.g. rangebreak-london")
        self.name = name
        zone, self.range_start, self.window_start, self.window_end = PRESETS[match.group(1)]
        self.zone = ZoneInfo(zone)

    def _clock(self, candle: Candle) -> tuple[int, int]:
        local = candle.timestamp.astimezone(self.zone)
        return local.hour, local.minute

    def analyze(self, candles: list[Candle]) -> MarketSetup:
        bar = candles[-1]

        def wait(reason: str) -> MarketSetup:
            return MarketSetup("NO_TRADE", bar.timestamp, reason, "NEUTRAL", self.name)

        local = bar.timestamp.astimezone(self.zone)
        if local.weekday() >= 5 or not self.window_start <= self._clock(bar) < self.window_end:
            return wait("Outside the breakout window")
        today = [c for c in candles[-1000:] if c.timestamp.astimezone(self.zone).date() == local.date()]
        ranged = [c for c in today if self.range_start <= self._clock(c) < self.window_start]
        if len(ranged) < MINIMUM_RANGE_CANDLES:
            return wait("Too few candles in today's range")
        high, low = max(c.high for c in ranged), min(c.low for c in ranged)
        for candle in today:
            if self._clock(candle) < self.window_start:
                continue
            up, down = candle.high >= high + candle.spread, candle.low <= low - candle.spread
            if not (up or down):
                continue
            if candle is not bar:
                return wait("The range already broke earlier today")
            if up and down:
                return wait("One candle broke both sides of the range")
            break
        else:
            return wait("The range has not broken yet")
        sign = 1 if up else -1
        stop = low if up else high
        risk = (bar.close - stop) * sign
        if risk <= 0:
            return wait("The close is back beyond the stop")
        return MarketSetup(
            "READY_LONG" if up else "READY_SHORT", bar.timestamp, "Range breakout",
            "BULLISH" if up else "BEARISH", self.name, entry=bar.close, stop=stop,
            target=bar.close + sign * FAR_TARGET_R * risk, atr=risk, zone_low=low, zone_high=high,
        )

    def bracket(self, candles: list[Candle], now: datetime | None = None) -> dict | None:
        """Stop-order plan (issue #101): while the window is open and today's range has not broken yet,
        a buy stop one spread above the range high and a sell stop one spread below the low, each with
        its stop at the other side of the range, valid until the window ends."""
        if len(candles) < self.minimum_candles:
            return None
        bar = candles[-1]
        local = (now or bar.timestamp).astimezone(self.zone)   # the last completed candle may still be in the range
        if local.weekday() >= 5 or not self.window_start <= (local.hour, local.minute) < self.window_end:
            return None
        today = [c for c in candles[-1000:] if c.timestamp.astimezone(self.zone).date() == local.date()]
        ranged = [c for c in today if self.range_start <= self._clock(c) < self.window_start]
        if len(ranged) < MINIMUM_RANGE_CANDLES:
            return None
        high, low = max(c.high for c in ranged), min(c.low for c in ranged)
        for candle in today:
            if self._clock(candle) >= self.window_start and (
                candle.high >= high + candle.spread or candle.low <= low - candle.spread
            ):
                return None  # already broke: no new orders today
        spread = bar.spread
        ends = local.replace(hour=self.window_end[0], minute=self.window_end[1], second=0, microsecond=0)
        return {
            "day": local.date().isoformat(), "high": high, "low": low, "spread": spread,
            "BUY": {"level": high + spread, "stop": low},
            "SELL": {"level": low - spread, "stop": high},
            "expires": ends.astimezone(timezone.utc).isoformat(),
        }
