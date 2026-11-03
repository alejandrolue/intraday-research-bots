"""Trading-hours filter for new entries.

Gold is most liquid from the London open to the New York afternoon. Entries outside that
window trade thin markets with wider spreads, and the minutes right after the London open and
around the 08:30 ET US data releases / COMEX open are volatile enough that ATR-based stops
(measured on the quieter candles before) are too tight. Times are local and follow DST.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")

RESOLUTION_MINUTES = {
    "MINUTE": 1,
    "MINUTE_5": 5,
    "MINUTE_15": 15,
    "MINUTE_30": 30,
    "HOUR": 60,
    "HOUR_4": 240,
    "DAY": 1440,
    "WEEK": 10080,
}


def resolution_delta(resolution: str) -> timedelta:
    try:
        return timedelta(minutes=RESOLUTION_MINUTES[resolution])
    except KeyError as exc:
        raise ValueError(f"Unknown Capital.com resolution {resolution!r}") from exc


@dataclass(frozen=True)
class TradingWindow:
    london_open: time = time(8, 0)
    new_york_close: time = time(16, 0)
    new_york_data: time = time(8, 30)
    guard_minutes: int = 15

    def block_reason(self, moment: datetime) -> str | None:
        """Why a new entry at `moment` is not allowed, or None when it is."""
        london = moment.astimezone(LONDON)
        new_york = moment.astimezone(NEW_YORK)
        if london.weekday() >= 5:
            return "Weekend: no new entries."
        if london.time() < self.london_open or new_york.time() >= self.new_york_close:
            return "Outside London-open to New York-afternoon trading hours."
        if self._within_guard(london, self.london_open):
            return f"Within {self.guard_minutes} minutes of the London open."
        if self._within_guard(new_york, self.new_york_data):
            return f"Within {self.guard_minutes} minutes of the 08:30 ET US data / COMEX open."
        return None

    def _within_guard(self, local: datetime, start: time) -> bool:
        opened = local.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
        return opened <= local < opened + timedelta(minutes=self.guard_minutes)


def trading_window(name: str, *, guard_minutes: int = 15) -> TradingWindow | None:
    if name == "all":
        return None
    if name == "london-ny":
        return TradingWindow(guard_minutes=guard_minutes)
    raise ValueError("Trading hours must be 'london-ny' or 'all'")
