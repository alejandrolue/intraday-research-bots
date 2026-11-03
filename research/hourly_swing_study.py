"""Issue #83: train, lock, validate the hourly range and breakout rules."""

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
from hourly_swing import RULES, features, simulate  # noqa: E402

HISTORY = ROOT / "data/history"
OUTPUT = ROOT / "research/results_hourly_swing"
PROTOCOL = ROOT / "research/HOURLY_SWING_PROTOCOL.md"
MARKETS = ("GOLD", "US100", "US500", "US30", "DE40")
TRAIN = (date(2019, 1, 1), date(2023, 1, 1))
VALID = (date(2023, 1, 1), date(2026, 9, 29))


def load(epic: str) -> list:
    rows = {}
    for path in sorted((HISTORY / epic / "HOUR").glob("*.csv")):
        for bar in _read(path):
            rows[bar.timestamp] = bar
    bars = [rows[key] for key in sorted(rows)]
    recorded = [bar.spread / bar.close for bar in bars if bar.spread > 0 and bar.close > 0]
    fraction = 2 * statistics.median(recorded)
    return [bar if bar.spread > 0 else replace(bar, spread=fraction * bar.close) for bar in bars]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources() -> dict:
    paths = [Path(__file__), PROTOCOL, ROOT / "research/hourly_swing.py", ROOT / "src/goldbot/regime.py"]
    return {str(p.relative_to(ROOT)): digest(p) for p in paths}


DATA = {epic: load(epic) for epic in MARKETS}
FEATURES = {epic: features(DATA[epic]) for epic in MARKETS}


def run(rule, period, *, rate=0.03, spread_mult=1.0) -> dict:
    trades = [t for epic in MARKETS for t in simulate(epic, DATA[epic], FEATURES[epic], rule, *period,
                                                      financing_rate=rate, spread_mult=spread_mult)]
    trades.sort(key=lambda t: t.closed)
    rs = [t.r for t in trades]
    wins, losses = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    years, markets, reasons = defaultdict(float), defaultdict(float), defaultdict(int)
    peak = equity = drawdown = 0.0
    for t in trades:
        years[t.closed.year] += t.r
        markets[t.market] += t.r
        reasons[t.reason] += 1
        equity += t.r
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"rule": rule.id, "family": rule.family, "trades": len(rs),
            "average_r": sum(rs) / len(rs) if rs else 0.0, "total_r": sum(rs),
            "profit_factor": wins / losses if losses else None, "max_drawdown_r": drawdown,
            "win_rate": sum(r > 0 for r in rs) / len(rs) if rs else 0.0,
            "long_r": sum(t.r for t in trades if t.direction == 1),
            "short_r": sum(t.r for t in trades if t.direction == -1),
            "years": {str(k): v for k, v in sorted(years.items())},
            "markets": {m: markets.get(m, 0.0) for m in MARKETS}, "exits": dict(reasons)}


def eligible(row: dict) -> bool:
    return (row["trades"] >= 200 and row["average_r"] > 0.08 and (row["profit_factor"] or 0) > 1.15
            and sum(v > 0 for v in row["years"].values()) >= 3
            and sum(v > 0 for v in row["markets"].values()) >= 3
            and row["stress_spread_average_r"] > 0)


def select(rows: list[dict]) -> list[dict]:
    chosen = []
    for family in ("range", "breakout"):
        group = [r for r in rows if r["family"] == family]
        allowed = [r for r in group if eligible(r)]
        if allowed:
            best = sorted(allowed, key=lambda r: (-r["total_r"], r["max_drawdown_r"], r["rule"]))[0]
        else:
            pool = [r for r in group if r["trades"] >= 200] or group
            best = sorted(pool, key=lambda r: (-r["average_r"], r["rule"]))[0]
        chosen.append({"rule": best["rule"], "eligible": bool(allowed)})
    return chosen


def table(rows: list[dict]) -> str:
    lines = ["# All 16 training variants (2019–2022, 5 markets, hourly)", "",
             "| Rule | Trades | Win | Avg R | Total R | PF | DD R | +Years | +Markets | Long R | Short R | 2× spread | Eligible |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in sorted(rows, key=lambda r: -r["average_r"]):
        pf = r["profit_factor"]
        lines.append(f"| {r['rule']} | {r['trades']} | {r['win_rate']:.0%} | {r['average_r']:+.3f} | "
                     f"{r['total_r']:+.1f} | {pf if pf is None else f'{pf:.2f}'} | {r['max_drawdown_r']:.1f} | "
                     f"{sum(v > 0 for v in r['years'].values())}/{len(r['years'])} | "
                     f"{sum(v > 0 for v in r['markets'].values())}/5 | {r['long_r']:+.1f} | {r['short_r']:+.1f} | "
                     f"{r['stress_spread_average_r']:+.3f} | {eligible(r)} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("train", "validate"))
    args = parser.parse_args()
    OUTPUT.mkdir(exist_ok=True)
    rules = {r.id: r for r in RULES}
    if args.stage == "train":
        if (OUTPUT / "selection.json").exists():
            raise SystemExit("Selection already locked")
        rows = []
        for rule in RULES:
            row = run(rule, TRAIN)
            row["stress_spread_average_r"] = run(rule, TRAIN, spread_mult=2.0)["average_r"]
            rows.append(row)
        (OUTPUT / "training.json").write_text(json.dumps(rows, indent=2) + "\n")
        (OUTPUT / "TRAINING.md").write_text(table(rows))
        (OUTPUT / "selection.json").write_text(json.dumps({
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "training_sha256": digest(OUTPUT / "training.json"), "sources": sources(),
            "selected": select(rows)}, indent=2) + "\n")
        print(table(rows))
        print("Locked:", select(rows))
    else:
        manifest = json.loads((OUTPUT / "selection.json").read_text())
        if manifest["sources"] != sources():
            raise SystemExit("Sources changed after selection")
        if (OUTPUT / "validation.json").exists():
            raise SystemExit("Validation already run")
        out = []
        for choice in manifest["selected"]:
            rule = rules[choice["rule"]]
            row = run(rule, VALID)
            spread = run(rule, VALID, spread_mult=2.0)
            fin = run(rule, VALID, rate=0.06)
            row.update(stress_spread_average_r=spread["average_r"], stress_financing_average_r=fin["average_r"],
                       eligible_in_training=choice["eligible"])
            row["passed"] = (choice["eligible"] and row["trades"] >= 150 and row["average_r"] > 0.05
                             and (row["profit_factor"] or 0) > 1.1
                             and sum(v > 0 for v in row["years"].values()) >= 3
                             and sum(v > 0 for v in row["markets"].values()) >= 3
                             and spread["average_r"] > 0 and fin["average_r"] > 0)
            out.append(row)
        (OUTPUT / "validation.json").write_text(json.dumps(out, indent=2) + "\n")
        for row in out:
            print(json.dumps({k: row[k] for k in ("rule", "trades", "win_rate", "average_r", "total_r",
                  "profit_factor", "max_drawdown_r", "long_r", "short_r", "stress_spread_average_r",
                  "stress_financing_average_r", "passed")}))
            print("   years", {k: round(v, 1) for k, v in row["years"].items()},
                  "markets", {k: round(v, 1) for k, v in row["markets"].items()})


if __name__ == "__main__":
    main()
