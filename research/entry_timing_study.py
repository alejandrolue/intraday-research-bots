"""Issue #92: run, lock and validate the entry-timing variants; replay the live window."""

from __future__ import annotations

import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research"))

from goldbot.history import _read  # noqa: E402
from entry_timing import Variant, execute, signals  # noqa: E402

U = timezone.utc
OUT = ROOT / "research/results_entry_timing"
PROTOCOL = ROOT / "research/ENTRY_TIMING_PROTOCOL.md"
Y1 = (datetime(2024, 9, 29, tzinfo=U), datetime(2025, 9, 29, tzinfo=U))
Y2 = (datetime(2025, 9, 29, tzinfo=U), datetime(2026, 9, 29, tzinfo=U))
LIVE = (datetime(2026, 9, 29, 17, 40, tzinfo=U), datetime(2026, 10, 9, 17, 0, tzinfo=U))
VARIANTS = [Variant(e, c, x) for e, c, x in product(("market", "L50", "M1"), (False, True), (False, True))]


def load(res: str) -> list:
    rows = {}
    for p in sorted((ROOT / f"data/history/GOLD/{res}").glob("*.csv")):
        for b in _read(p):
            rows[b.timestamp] = b
    bars = [rows[k] for k in sorted(rows)]
    for i, b in enumerate(bars):
        if b.spread <= 0 and i:
            bars[i] = replace(b, spread=bars[i - 1].spread)
    return bars


def stats(trades, n_signals) -> dict:
    rs = [t.r for t in trades]
    w, l = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    reasons = {}
    for t in trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1
    return {"signals": n_signals, "trades": len(rs), "total_r": sum(rs),
            "average_r": sum(rs) / len(rs) if rs else 0.0, "r_per_signal": sum(rs) / n_signals if n_signals else 0.0,
            "profit_factor": w / l if l else None, "win_rate": sum(r > 0 for r in rs) / len(rs) if rs else 0.0,
            "exits": reasons}


def job(args):
    variant, period = args
    m1 = [b for b in M1 if period[0] <= b.timestamp < period[1]]
    sigs = [s for s in SIGNALS if period[0] <= s.time < period[1]]
    return variant.id, stats(execute(m1, sigs, variant), len(sigs))


M5, M30, M1 = load("MINUTE_5"), load("MINUTE_30"), load("MINUTE")
SIGNALS = signals(M5, M30, Y1[0], LIVE[1])


def eligible(row: dict, base: dict) -> bool:
    return (row["average_r"] > 0 and (row["profit_factor"] or 0) > 1.1 and row["trades"] >= 200
            and row["r_per_signal"] - base["r_per_signal"] >= 0.05)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    if (OUT / "results.json").exists():
        raise SystemExit("Already run")
    jobs = [(v, p) for p in (Y1, Y2, LIVE) for v in VARIANTS]
    with ProcessPoolExecutor(8) as pool:
        done = list(pool.map(job, jobs))
    results = {"Y1": {}, "Y2": {}, "LIVE": {}}
    for (v, p), (vid, row) in zip(jobs, done):
        results["Y1" if p == Y1 else "Y2" if p == Y2 else "LIVE"][vid] = row
    base = "market-nocool-fixed"
    y1 = results["Y1"]
    allowed = [k for k, r in y1.items() if k != base and eligible(r, y1[base])]
    locked = sorted(allowed, key=lambda k: -y1[k]["total_r"])[:2] or [max((k for k in y1 if k != base), key=lambda k: y1[k]["total_r"])]
    passed = {k: bool(allowed) and k in allowed and eligible(results["Y2"][k], results["Y2"][base]) for k in locked}
    payload = {"protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
               "results": results, "eligible_y1": allowed, "locked": locked, "passed": passed}
    (OUT / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    for period in ("Y1", "Y2", "LIVE"):
        print(f"\n== {period}  (signals {results[period][base]['signals']})")
        for k, r in sorted(results[period].items(), key=lambda kv: -kv[1]["total_r"]):
            pf = r["profit_factor"]
            print(f"  {k:24s} trades {r['trades']:5d} win {r['win_rate']:.0%} total {r['total_r']:+8.1f}R "
                  f"avg {r['average_r']:+.3f} per signal {r['r_per_signal']:+.3f} PF {pf if pf is None else round(pf, 2)}")
    print("\neligible on Y1:", allowed, "\nlocked:", locked, "\npassed Y2:", passed)


if __name__ == "__main__":
    main()
