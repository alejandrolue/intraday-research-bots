"""Issue #189 follow-up: trade the range: fade the level on the touch (pre-registered on #189).

Same touches as level_holds.py. Enter at the level (sell at resistance, buy at support), stop X beyond, target X back
(X = 0.5 hourly ATR, 1:1), first touch on 5-minute bars, a bar hitting both = stop first, exit at 2 hours otherwise,
minus the 5-minute spread in R. Rule: >= +0.05R per trade with >= 200 trades in both 2024 and 2025-26 for a session.
Usage:  python research/levels/level_fade.py [--root LIVE_GOLDBOT_DIR]
"""

from __future__ import annotations

import argparse
import bisect
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from level_holds import MARKETS, ROOT, WINDOW, hourly_atr, levels, load, session


def trades(m5: list, atr: dict, lv: list[dict]) -> list[dict]:
    times = [b.timestamp for b in m5]
    out = []
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
                    break
            prev_close = b.close
            i += 1
        if touch is None:
            continue
        t0 = m5[touch].timestamp
        hour = t0.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        x = 0.5 * (atr.get(hour) or atr.get(hour - timedelta(hours=1)) or 0)
        if not x:
            continue
        # a fade sells at resistance (direction -1) and buys at support (+1)
        d = -side
        stop, target = level - d * x, level + d * x
        r, j = None, touch
        # the touch bar itself: only its part after reaching the level is known to follow the entry; stop first
        while j < len(m5) and m5[j].timestamp < t0 + WINDOW:
            b = m5[j]
            hit_stop = b.high >= stop if d < 0 else b.low <= stop
            hit_target = b.low <= target if d < 0 else b.high >= target
            if hit_stop:
                r = -1.0
                break
            if hit_target and j > touch:
                r = 1.0
                break
            j += 1
        if r is None:
            last = m5[min(j, len(m5) - 1) - 1] if j > touch else m5[touch]
            r = (last.close - level) * d / x
        out.append({"year": t0.year, "kind": L["kind"], "session": session(t0),
                    "r": r - (m5[touch].spread or 0) / x})
    return out


def summary(ts: list[dict], key) -> dict:
    agg = defaultdict(list)
    for t in ts:
        agg[key(t)].append(t["r"])
    return {k: {"trades": len(v), "avg_r": round(sum(v) / len(v), 4), "win_rate": round(sum(r > 0 for r in v) / len(v), 3)}
            for k, v in sorted(agg.items())}


def run(root: Path) -> dict:
    result = {"issue": 189, "check": "fade at the level, 1:1, stop first"}
    for epic in MARKETS:
        m5, hours = load(root, epic, "MINUTE_5"), load(root, epic, "HOUR")
        ts = trades(m5, hourly_atr(hours), levels(m5, hours))
        period = lambda t: "2024" if t["year"] == 2024 else "2025-26"
        by = summary(ts, lambda t: f"{t['session']} | {period(t)}")
        sessions = {k.split(" | ")[0] for k in by}
        passed = [s for s in sessions if all((by.get(f"{s} | {p}") or {}).get("trades", 0) >= 200
                                             and by[f"{s} | {p}"]["avg_r"] >= 0.05 for p in ("2024", "2025-26"))]
        result[epic] = {"trades": len(ts), "by_session_period": by,
                        "by_kind": summary(ts, lambda t: f"{t['kind']} | {period(t)}"), "sessions_passing": sorted(passed)}
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    print(json.dumps(run(Path(ap.parse_args().root)), indent=1))


if __name__ == "__main__":
    main()
