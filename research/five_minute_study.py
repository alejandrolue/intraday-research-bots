"""Issue #12: train, freeze, then validate a finite strategy grid using cached prices.

Run with the live venv's Python from outputs/goldbot:
    python research/five_minute_study.py train
    # Commit protocol, code and training outputs before opening the holdout.
    python research/five_minute_study.py validate
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from goldbot.backtest import BacktestConfig, run_backtest  # noqa: E402
from goldbot.history import _read  # noqa: E402
from goldbot.risk import RiskConfig  # noqa: E402
from five_minute_strategies import SessionStrategy, Variant, variants  # noqa: E402

HISTORY = ROOT / "data/history/GOLD"
OUTPUT = ROOT / "research/results_5m"
START = datetime(2024, 9, 29, tzinfo=timezone.utc)
SPLIT = START.replace(year=2025)
END = START.replace(year=2026)
ENTRY = []
TREND = []
PERIOD_START = START
PERIOD_END = SPLIT


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict:
    paths = [Path(__file__), ROOT / "research/five_minute_strategies.py",
             ROOT / "research/FIVE_MINUTE_PROTOCOL.md"]
    paths += sorted((ROOT / "src/goldbot").glob("*.py"))
    return {str(path.relative_to(ROOT)): digest(path) for path in paths}


REPAIRED: dict[str, list[str]] = {}


def load(resolution: str, end: datetime) -> list:
    indexed = {}
    for path in sorted((HISTORY / resolution).glob("*.csv")):
        if path.stem > end.strftime("%Y-%m"):
            continue
        for bar in _read(path):
            if bar.timestamp >= end:
                continue
            if bar.timestamp in indexed and indexed[bar.timestamp] != bar:
                raise ValueError(f"Conflicting cached candles at {bar.timestamp}")
            indexed[bar.timestamp] = bar
    if not indexed:
        raise ValueError(f"No cached {resolution} data")
    bars = [indexed[key] for key in sorted(indexed)]
    # Capital.com history contains a few crossed quotes (bid above ask). Their true spread is
    # unknown, so carry the previous candle's real spread forward and report every repair
    # (protocol amendment A1, made before any candidate returns were evaluated).
    repaired = []
    for index, bar in enumerate(bars):
        if bar.spread > 0:
            continue
        if index == 0 or bars[index - 1].spread <= 0:
            raise ValueError(f"Nonpositive spread without a usable previous candle at {bar.timestamp}")
        bars[index] = replace(bar, spread=bars[index - 1].spread)
        repaired.append(bar.timestamp.isoformat())
    REPAIRED[resolution] = repaired
    return bars


def initialize(start: datetime, end: datetime) -> None:
    global ENTRY, TREND, PERIOD_START, PERIOD_END
    all_entry = load("MINUTE_5", end)
    before = [b for b in all_entry if b.timestamp < start]
    if len(before) < 300:
        raise ValueError("Need 300 warmup bars")
    ENTRY = before[-300:] + [b for b in all_entry if start <= b.timestamp < end]
    TREND = load("HOUR_4", end)
    PERIOD_START, PERIOD_END = start, end


def audit() -> dict:
    data = ENTRY[300:]
    canonical = json.dumps([b.to_dict() for b in ENTRY], sort_keys=True).encode()
    return {
        "start_inclusive": PERIOD_START.isoformat(), "end_exclusive": PERIOD_END.isoformat(),
        "bars": len(data), "first_bar": data[0].timestamp.isoformat(),
        "last_bar": data[-1].timestamp.isoformat(), "warmup": 300,
        "median_spread": statistics.median(b.spread for b in data),
        "max_spread": max(b.spread for b in data),
        "gaps_over_5m": sum(b.timestamp - a.timestamp > timedelta(minutes=5)
                            for a, b in zip(data, data[1:])),
        "repaired_nonpositive_spreads": REPAIRED.get("MINUTE_5", []),
        "entry_sha256": hashlib.sha256(canonical).hexdigest(),
        "trend_sha256": hashlib.sha256(
            json.dumps([b.to_dict() for b in TREND], sort_keys=True).encode()).hexdigest(),
    }


def quarters(start: datetime) -> list[datetime]:
    return [start, start.replace(month=12), start.replace(year=start.year + 1, month=3),
            start.replace(year=start.year + 1, month=6), start.replace(year=start.year + 1)]


def bootstrap(trades: list) -> list[float]:
    # Resample calendar-week clusters, including empty weeks, to preserve within-week dependence.
    weeks = defaultdict(lambda: [0.0, 0])
    cursor = PERIOD_START.date()
    while cursor < PERIOD_END.date():
        iso = cursor.isocalendar()
        weeks[(iso.year, iso.week)]
        cursor += timedelta(days=1)
    for trade in trades:
        iso = trade.closed_at.isocalendar()
        bucket = weeks[(iso.year, iso.week)]
        bucket[0] += trade.r
        bucket[1] += 1
    buckets = list(weeks.values())
    rng = random.Random(12)
    values = []
    for _ in range(2000):
        draw = rng.choices(buckets, k=len(buckets))
        count = sum(b[1] for b in draw)
        if count:
            values.append(sum(b[0] for b in draw) / count)
    values.sort()
    return [values[int(len(values) * q)] for q in (0.025, 0.975)] if values else [0, 0]


def run(variant: Variant) -> dict:
    config = BacktestConfig(
        resolution="MINUTE_5", trend_resolution=variant.trend, management=None,
        risk=RiskConfig(max_positions=1, max_trades_per_day=3), label=variant.id,
    )
    result = run_backtest(ENTRY, TREND if variant.trend else None, config,
                          strategies=[SessionStrategy(variant, start=PERIOD_START)])
    row = result.metrics()
    row["variant"] = asdict(variant)
    row["side"] = {}
    for side in ("BUY", "SELL"):
        trades = [t for t in result.trades if t.direction == side]
        row["side"][side] = {"trades": len(trades), "total_r": sum(t.r for t in trades)}
    bounds = quarters(PERIOD_START)
    row["quarters"] = [sum(t.r for t in result.trades if a <= t.closed_at < b)
                       for a, b in zip(bounds, bounds[1:])]
    spreads = {b.timestamp: b.spread for b in ENTRY}
    costs = [spreads[t.opened_at] / abs(t.entry - t.initial_stop) for t in result.trades]
    holds = [(t.closed_at - t.opened_at).total_seconds() / 3600 for t in result.trades]
    row["median_spread_r"] = statistics.median(costs) if costs else 0
    row["median_hold_hours"] = statistics.median(holds) if holds else 0
    row["max_hold_hours"] = max(holds, default=0)
    row["overnight_utc"] = sum(t.closed_at.date() != t.opened_at.date() for t in result.trades)
    row["extra_020_cost_average_r"] = (
        sum(t.r - 0.20 / abs(t.entry - t.initial_stop) for t in result.trades) / len(result.trades)
        if result.trades else 0
    )
    if PERIOD_START == SPLIT:
        row["week_bootstrap_95_interval"] = bootstrap(result.trades)
    return row


def eligible(row: dict) -> bool:
    return (row["trades"] >= 150 and row["average_r"] > 0.05
            and all(s["total_r"] > 0 for s in row["side"].values())
            and sum(r > 0 for r in row["quarters"]) >= 3)


def select(rows: list[dict]) -> list[dict]:
    selected = []
    for family in ("orb", "rolling_sweep"):
        candidates = [r for r in rows if r["variant"]["family"] == family]
        allowed = [r for r in candidates if eligible(r)]
        pool = allowed or [r for r in candidates if r["trades"] >= 150]
        if pool:
            best = sorted(pool, key=lambda r: (-r["average_r"], r["max_drawdown_r"], r["label"]))[0]
        else:
            best = sorted(candidates, key=lambda r: (-r["trades"], -r["average_r"], r["label"]))[0]
        selected.append({"variant": best["variant"], "eligible": bool(allowed), "id": best["label"]})
    return selected


def write_json(path: Path, value) -> None:
    # Machine outputs contain aggregate research results, never credentials or runtime ledgers.
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def training_table(rows: list[dict]) -> str:
    lines = ["# All 48 year-1 variants", "", "| Variant | N | Avg R | Total R | PF | DD R | Long R | Short R | Positive Q | Eligible |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in sorted(rows, key=lambda r: -r["average_r"]):
        lines.append(f"| {r['label']} | {r['trades']} | {r['average_r']:+.3f} | {r['total_r']:+.1f} | "
                     f"{r['profit_factor']:.2f} | {r['max_drawdown_r']:.1f} | "
                     f"{r['side']['BUY']['total_r']:+.1f} | {r['side']['SELL']['total_r']:+.1f} | "
                     f"{sum(q > 0 for q in r['quarters'])}/4 | {eligible(r)} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("train", "validate"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    OUTPUT.mkdir(exist_ok=True)
    if args.stage == "train":
        if (OUTPUT / "selection.json").exists():
            raise SystemExit("Selection already locked; refusing to overwrite the experiment")
        initialize(START, SPLIT)
        metadata = audit()
        rows = []
        with ProcessPoolExecutor(max_workers=args.workers, initializer=initialize,
                                 initargs=(START, SPLIT)) as pool:
            for row in pool.map(run, variants()):
                rows.append(row)
                write_json(OUTPUT / "training.json", {"data": metadata, "rows": rows})
                print(f"{len(rows)}/48 {row['label']} N={row['trades']} avgR={row['average_r']:+.3f}", flush=True)
        (OUTPUT / "TRAINING.md").write_text(training_table(rows))
        write_json(OUTPUT / "selection.json", {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "trial_count": len(rows), "training_sha256": digest(OUTPUT / "training.json"),
            "source_hashes": source_hashes(), "selected": select(rows),
        })
        print("Selections locked. Commit before running validation.", flush=True)
    else:
        manifest = json.loads((OUTPUT / "selection.json").read_text())
        if manifest["source_hashes"] != source_hashes():
            raise SystemExit("Sources changed after selection; do not silently retune the holdout")
        if manifest["training_sha256"] != digest(OUTPUT / "training.json"):
            raise SystemExit("Training results changed after selection")
        if (OUTPUT / "validation.json").exists():
            raise SystemExit("Holdout already evaluated; refusing an unrecorded repeat")
        initialize(SPLIT, END)
        rows = [run(Variant(**s["variant"])) for s in manifest["selected"]]
        write_json(OUTPUT / "validation.json", {"data": audit(), "rows": rows})
        for row in rows:
            print(f"{row['label']} N={row['trades']} avgR={row['average_r']:+.3f}", flush=True)


if __name__ == "__main__":
    main()
