"""Market opening hours from Capital.com, so bots stay quiet while their market is closed.

Capital.com publishes each instrument's weekly schedule, e.g. GOLD:
    {"zone": "UTC", "mon": ["00:00 - 20:59", "22:00 - 00:00"], ..., "sat": [], "sun": ["22:00 - 00:00"]}
An end of "00:00" means midnight at the end of the day; any other end minute is inclusive
("20:59" = open until 20:59:59).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class MarketHours:
    def __init__(self, schedule: dict[str, Any]) -> None:
        zone = str(schedule.get("zone") or "UTC")
        self.zone = timezone.utc if zone.upper() == "UTC" else ZoneInfo(zone)
        self.ranges: dict[int, list[tuple[int, int]]] = {}
        for index, day in enumerate(DAYS):
            spans = []
            for text in schedule.get(day) or []:
                start, end = (part.strip() for part in str(text).split("-"))
                start_minute = _minutes(start)
                end_minute = 24 * 60 if end == "00:00" else _minutes(end) + 1
                spans.append((start_minute, end_minute))
            self.ranges[index] = spans

    def is_open(self, moment: datetime) -> bool:
        local = moment.astimezone(self.zone)
        minute = local.hour * 60 + local.minute
        return any(start <= minute < end for start, end in self.ranges[local.weekday()])

    def next_open(self, moment: datetime) -> datetime | None:
        """The next moment the market opens (now if it is open), searching one week ahead."""
        local = moment.astimezone(self.zone)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        minute_now = local.hour * 60 + local.minute
        for offset in range(8):
            day = midnight + timedelta(days=offset)
            for start, end in sorted(self.ranges[day.weekday()]):
                if offset == 0 and end <= minute_now:
                    continue
                if offset == 0 and start <= minute_now < end:
                    return moment
                opened = day + timedelta(minutes=start)
                if opened > local:
                    return opened.astimezone(timezone.utc)
        return None


    def long_close(self, moment: datetime, *, min_gap: timedelta) -> datetime | None:
        """When the current trading span ends, if the closure after it lasts at least `min_gap`
        (the weekend, not the daily break). None while closed or before a short break."""
        local = moment.astimezone(self.zone)
        minute = local.hour * 60 + local.minute
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        current = next(
            ((start, end) for start, end in self.ranges[local.weekday()] if start <= minute < end), None
        )
        if current is None:
            return None
        span_end = midnight + timedelta(minutes=current[1])
        reopen = self.next_open(span_end)
        if reopen is not None and reopen.astimezone(self.zone) - span_end < min_gap:
            return None  # a short break, or trading continues after midnight
        return span_end.astimezone(timezone.utc)


def _minutes(text: str) -> int:
    parsed = time.fromisoformat(text)
    return parsed.hour * 60 + parsed.minute


class MarketClock:
    """Caches each market's schedule (refreshed every 6 hours) for the bot loops."""

    REFRESH = timedelta(hours=6)

    def __init__(self, fetch_details: Any) -> None:
        self._fetch = fetch_details
        self._hours: dict[str, tuple[datetime, MarketHours | None]] = {}

    def hours(self, epic: str, now: datetime) -> MarketHours | None:
        if self._fetch is None:
            return None
        cached = self._hours.get(epic)
        if cached is None or now - cached[0] > self.REFRESH:
            try:
                schedule = (self._fetch(epic).get("instrument") or {}).get("openingHours")
                hours = MarketHours(schedule) if schedule else None
            except Exception:  # noqa: BLE001 - unknown schedule means "treat as open"
                hours = cached[1] if cached else None
            self._hours[epic] = (now, hours)
        return self._hours[epic][1]

    def weekend_close(self, epic: str, now: datetime) -> datetime | None:
        """The coming close before a closure of at least 6 hours (the weekend), if trading is open."""
        hours = self.hours(epic, now)
        if hours is None:
            return None
        return hours.long_close(now, min_gap=timedelta(hours=6))

    def closed_until(self, epic: str, now: datetime) -> datetime | None:
        """None while the market is open (or its schedule is unknown), else the reopening time."""
        hours = self.hours(epic, now)
        if hours is None or hours.is_open(now):
            return None
        return hours.next_open(now) or now + timedelta(minutes=5)
