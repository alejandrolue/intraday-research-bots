"""Issue #189: do support/resistance levels and FVGs hold midday and break at the opens? (descriptive, pre-registered)

Levels per trading day (22:00 UTC boundary): previous-day high/low, Asian range high/low (22:00–07:00 UTC, used after
07:00 UTC) and unfilled hourly FVGs (gap between hourly bars i−2 and i, active from the next hour, at most the 3 most
recent per side, up to 3 days). A touch is the first 5-minute bar that reaches a level from the far side. Within 2
hours, with X = 0.5 × hourly ATR(14): hold = price moves X away in the rejection direction before a 5-minute close X
beyond the level; break = that close comes first. Hold rate = holds / (holds + breaks), by New York session.
Usage:  python research/levels/level_holds.py [--root LIVE_GOLDBOT_DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from goldbot.history import _read  # noqa: E402

NY = ZoneInfo("America/New_York")
MARKETS = ("GOLD", "US100")
WINDOW = timedelta(hours=2)
FVG_PER_SIDE, FVG_DAYS = 3, 3
SESSIONS = [  # New York local time
    ("Asia", (18, 0), (26, 0)), ("London open", (2, 0), (4, 0)), ("London", (4, 0), (8, 30)),
    ("US open", (8, 30), (10, 30)), ("US midday", (10, 30), (14, 0)), ("US afternoon", (14, 0), (17, 0)),
]


def session(t) -> str:
    local = t.astimezone(NY)
    m = local.hour * 60 + local.minute
    for name, (h0, m0), (h1, m1) in SESSIONS:
        a, b = h0 * 60 + m0, h1 * 60 + m1
        if a <= m < b or (b > 24 * 60 and m < b - 24 * 60):
            return name
    return "other"


def load(root: Path, epic: str, res: str) -> list:
    rows = {}
    for p in sorted((root / "data/history" / epic / res).glob("*.csv")):
        for b in _read(p):
            rows[b.timestamp] = b
    return [rows[k] for k in sorted(rows)]


def hourly_atr(hours: list) -> dict:
    out, atr, prev = {}, None, None
    for b in hours:
        tr = b.high - b.low if prev is None else max(b.high - b.low, abs(b.high - prev), abs(b.low - prev))
        atr = tr if atr is None else atr + (tr - atr) / 14
        out[b.timestamp] = atr
        prev = b.close
    return out


def day_key(t):
    return (t + timedelta(hours=2)).date()


def levels(m5: list, hours: list) -> list[dict]:
    """Every level with its kind, side (+1 resistance, −1 support), price and active window."""
    out = []
    by_day = defaultdict(list)
    for b in m5:
        by_day[day_key(b.timestamp)].append(b)
    days = sorted(by_day)
    for prev, day in zip(days, days[1:]):
        bars = by_day[day]
        start, end = bars[0].timestamp, bars[-1].timestamp + timedelta(minutes=5)
        ph, pl = max(b.high for b in by_day[prev]), min(b.low for b in by_day[prev])
        out += [{"kind": "prev-day", "side": 1, "level": ph, "from": start, "to": end},
                {"kind": "prev-day", "side": -1, "level": pl, "from": start, "to": end}]
        asia = [b for b in bars if b.timestamp.hour >= 22 or b.timestamp.hour < 7]
        if len(asia) >= 12:
            after = next((b.timestamp for b in bars if 7 <= b.timestamp.hour < 22), None)
            if after:
                out += [{"kind": "asia", "side": 1, "level": max(b.high for b in asia), "from": after, "to": end},
                        {"kind": "asia", "side": -1, "level": min(b.low for b in asia), "from": after, "to": end}]
    recent = {1: [], -1: []}
    for i in range(2, len(hours)):
        a, c = hours[i - 2], hours[i]
        if c.timestamp - a.timestamp != timedelta(hours=2):
            continue
        active_from = c.timestamp + timedelta(hours=1)
        if c.low > a.high:      # bullish gap: support, first touched at its top
            recent[-1] = (recent[-1] + [{"kind": "fvg", "side": -1, "level": c.low, "far": a.high,
                                         "from": active_from, "to": active_from + timedelta(days=FVG_DAYS)}])[-FVG_PER_SIDE:]
            out.append(recent[-1][-1])
        elif c.high < a.low:    # bearish gap: resistance, first touched at its bottom
            recent[1] = (recent[1] + [{"kind": "fvg", "side": 1, "level": c.high, "far": a.low,
                                       "from": active_from, "to": active_from + timedelta(days=FVG_DAYS)}])[-FVG_PER_SIDE:]
            out.append(recent[1][-1])
    return out


def outcomes(m5: list, atr: dict, lv: list[dict]) -> list[dict]:
    import bisect

    times = [b.timestamp for b in m5]
    events = []
    for L in lv:
        i = bisect.bisect_left(times, L["from"])
        side, level = L["side"], L["level"]
        prev_close = m5[i - 1].close if i > 0 else None
        touch = None
        while i < len(m5) and m5[i].timestamp < L["to"]:
            b = m5[i]
            if prev_close is not None:
                came_from_far = prev_close < level if side > 0 else prev_close > level
                reached = b.high >= level if side > 0 else b.low <= level
                if came_from_far and reached:
                    touch = i
                    break
                if not came_from_far and L["kind"] == "fvg":
                    break       # price already beyond the gap's edge: filled or never approached from the far side
            prev_close = b.close
            i += 1
        if touch is None:
            continue
        t0 = m5[touch].timestamp
        hour = t0.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        x = 0.5 * atr.get(hour, atr.get(hour - timedelta(hours=1), 0) or 0)
        if not x:
            continue
        result = None
        j = touch
        while j < len(m5) and m5[j].timestamp < t0 + WINDOW:
            b = m5[j]
            broke = b.close >= level + x if side > 0 else b.close <= level - x
            held = b.low <= level - x if side > 0 else b.high >= level + x
            if broke:
                result = "break"
                break
            if held:
                result = "hold"
                break
            j += 1
        events.append({"time": t0.isoformat(), "year": t0.year, "kind": L["kind"], "side": side,
                       "session": session(t0), "result": result or "neither"})
    return events


def table(events: list[dict], key) -> dict:
    agg = defaultdict(lambda: {"hold": 0, "break": 0, "neither": 0})
    for e in events:
        agg[key(e)][e["result"]] += 1
    out = {}
    for k, v in sorted(agg.items()):
        n = v["hold"] + v["break"]
        out[k] = {**v, "hold_rate": round(v["hold"] / n, 3) if n else None}
    return out


def run(root: Path) -> dict:
    result = {"issue": 189}
    for epic in MARKETS:
        m5, hours = load(root, epic, "MINUTE_5"), load(root, epic, "HOUR")
        if not m5:
            continue
        ev = outcomes(m5, hourly_atr(hours), levels(m5, hours))
        period = lambda e: "2024" if e["year"] == 2024 else "2025-26"
        result[epic] = {
            "period": f"{m5[0].timestamp:%Y-%m-%d} .. {m5[-1].timestamp:%Y-%m-%d}", "events": len(ev),
            "by_session": table(ev, lambda e: e["session"]),
            "by_session_period": table(ev, lambda e: f"{e['session']} | {period(e)}"),
            "by_kind_session": table(ev, lambda e: f"{e['kind']} | {e['session']}"),
        }
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    print(json.dumps(run(Path(ap.parse_args().root)), indent=1))


if __name__ == "__main__":
    main()
