"""Market opens and US news blackout windows (issue #63), defined in local exchange time.

Times are stored in their own time zone and converted with zoneinfo, so both DST changes
(Europe on 25 Oct 2026, the US on 1 Nov 2026) are handled without hard-coded UTC offsets.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")

# name, zone, local open time
MARKET_OPENS = (
    ("tokyo", ZoneInfo("Asia/Tokyo"), time(9, 0)),
    ("london", ZoneInfo("Europe/London"), time(8, 0)),
    ("comex", NEW_YORK, time(8, 20)),
    ("us-data", NEW_YORK, time(8, 30)),
    ("ny-cash", NEW_YORK, time(9, 30)),
)

# High-impact US events (Eastern Time): (day, start, end) of the release; the blackout adds
# 15 minutes before and 30 minutes after. FOMC ends with the 14:30 press conference.
# Add new dates with a pull request.
NEWS_EVENTS = (
    ("NFP", date(2026, 10, 2), time(8, 30), time(8, 30)),
    ("CPI", date(2026, 10, 14), time(8, 30), time(8, 30)),
    ("PPI/Retail", date(2026, 10, 15), time(8, 30), time(8, 30)),
    ("FOMC", date(2026, 10, 28), time(14, 0), time(14, 30)),
    ("NFP", date(2026, 11, 6), time(8, 30), time(8, 30)),
    ("CPI", date(2026, 11, 10), time(8, 30), time(8, 30)),
    ("PPI", date(2026, 11, 12), time(8, 30), time(8, 30)),
    ("FOMC", date(2026, 11, 18), time(14, 0), time(14, 30)),
)
ALL_DAY_EVENTS = (("US elections", date(2026, 11, 3)),)
BEFORE = timedelta(minutes=15)
AFTER = timedelta(minutes=30)


def news_blackout(moment: datetime) -> str | None:
    """The event name if `moment` falls inside a blackout window, else None."""
    moment = moment.astimezone(timezone.utc)
    for name, day, start, end in NEWS_EVENTS:
        begin = datetime.combine(day, start, NEW_YORK) - BEFORE
        finish = datetime.combine(day, end, NEW_YORK) + AFTER
        if begin <= moment < finish:
            return name
    for name, day in ALL_DAY_EVENTS:
        begin = datetime.combine(day, time(0, 0), NEW_YORK)
        if begin <= moment < begin + timedelta(days=1):
            return name
    return None


def nearest_open(moment: datetime) -> tuple[str, int]:
    """The market open closest to `moment` and the signed minutes since it (negative = before)."""
    moment = moment.astimezone(timezone.utc)
    best: tuple[str, int] | None = None
    for name, zone, opens_at in MARKET_OPENS:
        local_day = moment.astimezone(zone).date()
        for offset in (-1, 0, 1):
            opened = datetime.combine(local_day + timedelta(days=offset), opens_at, zone)
            minutes = int((moment - opened).total_seconds() // 60)
            if best is None or abs(minutes) < abs(best[1]):
                best = (name, minutes)
    assert best is not None
    return best


def minutes_after_open(moment: datetime) -> int | None:
    """Minutes since the most recent open within the last hour, else None."""
    moment = moment.astimezone(timezone.utc)
    recent = []
    for _, zone, opens_at in MARKET_OPENS:
        opened = datetime.combine(moment.astimezone(zone).date(), opens_at, zone)
        minutes = (moment - opened).total_seconds() / 60
        if 0 <= minutes < 60:
            recent.append(int(minutes))
    return min(recent) if recent else None
