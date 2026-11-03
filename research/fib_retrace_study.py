"""Issue #94: train, lock, validate the Fibonacci retracement rules on hourly GOLD."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research"))

from goldbot.history import _read  # noqa: E402
from fib_retrace import RULES, ema_series, setups, simulate, trend_bias  # noqa: E402

OUT = ROOT / "research/results_fib_retrace"
PROTOCOL = ROOT / "research/FIB_RETRACE_PROTOCOL.md"
TRAIN = (date(2019, 1, 1), date(2023, 1, 1))
VALID = (date(2023, 1, 1), date(2026, 9, 29))


def load() -> list:
    rows = {}
    for p in sorted((ROOT / "data/history/GOLD/HOUR").glob("*.csv")):
        for b in _read(p):
            rows[b.timestamp] = b
    bars = [rows[k] for k in sorted(rows)]
    recorded = [b.spread / b.close for b in bars if b.spread > 0]
    fraction = 2 * statistics.median(recorded)
    return [b if b.spread > 0 else replace(b, spread=fraction * b.close) for b in bars]


BARS = load()
LEGS = setups(BARS)
BIAS = trend_bias(BARS)
EMA50 = ema_series([b.close for b in BARS], 50)


def run(rule, period, *, rate=0.03, spread_mult=1.0) -> dict:
    trades = simulate(BARS, rule, LEGS, BIAS, EMA50, *period, financing_rate=rate, spread_mult=spread_mult)
    rs = [t.r for t in trades]
    w, l = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    years, reasons = defaultdict(float), defaultdict(int)
    peak = eq = dd = 0.0
    for t in trades:
        years[str(t.closed.year)] += t.r
        reasons[t.reason] += 1
        eq += t.r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"rule": rule.id, "trades": len(rs), "average_r": sum(rs) / len(rs) if rs else 0.0,
            "total_r": sum(rs), "profit_factor": w / l if l else None, "max_drawdown_r": dd,
            "win_rate": sum(r > 0 for r in rs) / len(rs) if rs else 0.0,
            "long_r": sum(t.r for t in trades if t.direction == 1),
            "short_r": sum(t.r for t in trades if t.direction == -1),
            "long_trades": sum(t.direction == 1 for t in trades),
            "years": dict(sorted(years.items())), "exits": dict(reasons)}


def eligible(r: dict) -> bool:
    return (r["trades"] >= 100 and r["average_r"] > 0.10 and (r["profit_factor"] or 0) > 1.2
            and sum(v > 0 for v in r["years"].values()) >= 3 and r["long_r"] >= 0 and r["short_r"] >= 0
            and r["stress_spread_average_r"] > 0)


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sources() -> dict:
    return {str(p.relative_to(ROOT)): digest(p)
            for p in (Path(__file__), PROTOCOL, ROOT / "research/fib_retrace.py")}


def table(rows) -> str:
    lines = ["# All 18 training variants (hourly GOLD, 2019–2022)", "",
             "| Rule | Trades | Win | Avg R | Total R | PF | DD R | Long R (n) | Short R | +Years | 2× spread | Exits | Eligible |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    for r in sorted(rows, key=lambda r: -r["average_r"]):
        pf = r["profit_factor"]
        lines.append(f"| {r['rule']} | {r['trades']} | {r['win_rate']:.0%} | {r['average_r']:+.3f} | {r['total_r']:+.1f} | "
                     f"{pf if pf is None else f'{pf:.2f}'} | {r['max_drawdown_r']:.1f} | {r['long_r']:+.1f} ({r['long_trades']}) | "
                     f"{r['short_r']:+.1f} | {sum(v > 0 for v in r['years'].values())}/{len(r['years'])} | "
                     f"{r['stress_spread_average_r']:+.3f} | {r['exits']} | {eligible(r)} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("train", "validate"))
    stage = ap.parse_args().stage
    OUT.mkdir(exist_ok=True)
    rules = {r.id: r for r in RULES}
    if stage == "train":
        if (OUT / "selection.json").exists():
            raise SystemExit("Selection already locked")
        rows = []
        for rule in RULES:
            row = run(rule, TRAIN)
            row["stress_spread_average_r"] = run(rule, TRAIN, spread_mult=2.0)["average_r"]
            rows.append(row)
        (OUT / "training.json").write_text(json.dumps(rows, indent=2) + "\n")
        (OUT / "TRAINING.md").write_text(table(rows))
        allowed = sorted((r for r in rows if eligible(r)), key=lambda r: -r["total_r"])[:2]
        if allowed:
            locked = [{"rule": r["rule"], "eligible": True} for r in allowed]
        else:
            pool = [r for r in rows if r["trades"] >= 100] or rows
            locked = [{"rule": max(pool, key=lambda r: r["average_r"])["rule"], "eligible": False}]
        (OUT / "selection.json").write_text(json.dumps({
            "created_utc": datetime.now(timezone.utc).isoformat(), "sources": sources(),
            "training_sha256": digest(OUT / "training.json"), "locked": locked}, indent=2) + "\n")
        print(table(rows))
        print("legs:", len(LEGS), "locked:", locked)
    else:
        m = json.loads((OUT / "selection.json").read_text())
        if m["sources"] != sources():
            raise SystemExit("Sources changed after selection")
        if (OUT / "validation.json").exists():
            raise SystemExit("Validation already run")
        out = []
        for choice in m["locked"]:
            rule = rules[choice["rule"]]
            row = run(rule, VALID)
            sp = run(rule, VALID, spread_mult=2.0)
            fin = run(rule, VALID, rate=0.06)
            row.update(stress_spread_average_r=sp["average_r"], stress_financing_average_r=fin["average_r"],
                       eligible_in_training=choice["eligible"])
            row["passed"] = (choice["eligible"] and row["trades"] >= 80 and row["average_r"] > 0.05
                             and (row["profit_factor"] or 0) > 1.1
                             and sum(v > 0 for v in row["years"].values()) >= 3
                             and row["long_r"] >= 0 and row["short_r"] >= 0
                             and sp["average_r"] > 0 and fin["average_r"] > 0)
            out.append(row)
        (OUT / "validation.json").write_text(json.dumps(out, indent=2) + "\n")
        for r in out:
            print(json.dumps({k: r[k] for k in ("rule", "trades", "win_rate", "average_r", "total_r", "profit_factor",
                  "max_drawdown_r", "long_r", "short_r", "long_trades", "years", "exits",
                  "stress_spread_average_r", "stress_financing_average_r", "passed")}))


if __name__ == "__main__":
    main()
