"""Issue #27: train, freeze, then validate the gold structure grid (STRUCTURE_PROTOCOL.md).

Run from outputs/goldbot with the live venv, once per timeframe:
    python research/structure_study.py 5m train
    # commit the training outputs and selection before opening the holdout
    python research/structure_study.py 5m validate
    (same for 1m)
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
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from goldbot.backtest import BacktestConfig, run_backtest  # noqa: E402
from goldbot.history import _read  # noqa: E402
from goldbot.risk import RiskConfig  # noqa: E402
from goldbot.structure_strategies import StructureStrategy, StructureVariant  # noqa: E402

HISTORY = ROOT / "data" / "history" / "GOLD"
EXTRA_COST_POINTS = 0.20


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


TIMEFRAMES = {
    "5m": {
        "resolution": "MINUTE_5",
        "windows": (288, 864),
        "radii": (3, 6),
        "periods": ((utc(2024, 9, 29), utc(2025, 9, 29)), (utc(2025, 9, 29), utc(2026, 9, 29))),
        "bucket": "quarter",
        "min_positive_buckets": 3,     # of 4 quarters (training)
        "pass_positive_buckets": 5,    # of 8 quarters (both periods)
    },
    "1m": {
        "resolution": "MINUTE",
        "windows": (1440, 4320),
        "radii": (5, 15),
        "periods": ((utc(2025, 10, 1), utc(2026, 4, 1)), (utc(2026, 4, 1), utc(2026, 9, 29))),
        "bucket": "month",
        "min_positive_buckets": 4,     # of 6 months (training)
        "pass_positive_buckets": 8,    # of 12 months (both periods)
    },
}
STATE: dict = {}


@dataclass(frozen=True)
class Variant:
    timeframe: str
    family: str
    window: int
    radius: int
    min_rr: float
    trend: str | None

    @property
    def structure(self) -> StructureVariant:
        return StructureVariant(self.family, self.window, self.radius, self.min_rr)

    @property
    def id(self) -> str:
        return f"{self.timeframe}-{self.structure.id}-{self.trend or 'none'}"


def variants(timeframe: str) -> list[Variant]:
    spec = TIMEFRAMES[timeframe]
    return [
        Variant(timeframe, *values)
        for values in product(("zone", "channel"), spec["windows"], spec["radii"], (1.5, 2.5),
                              (None, "HOUR_4"))
    ]


def output(timeframe: str) -> Path:
    return ROOT / "research" / f"results_structure_{timeframe}"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict:
    paths = [Path(__file__), ROOT / "research/STRUCTURE_PROTOCOL.md"]
    paths += sorted((ROOT / "src/goldbot").glob("*.py"))
    return {str(path.relative_to(ROOT)): digest(path) for path in paths}


def load(resolution: str, end: datetime) -> tuple[list, list[str]]:
    indexed = {}
    for path in sorted((HISTORY / resolution).glob("*.csv")):
        if path.stem > end.strftime("%Y-%m"):
            continue
        for bar in _read(path):
            if bar.timestamp < end:
                if bar.timestamp in indexed and indexed[bar.timestamp] != bar:
                    raise ValueError(f"Conflicting cached candles at {bar.timestamp}")
                indexed[bar.timestamp] = bar
    bars = [indexed[key] for key in sorted(indexed)]
    repaired = []
    for index, bar in enumerate(bars):  # amendment A1 from issue #12
        if bar.spread > 0:
            continue
        if index == 0 or bars[index - 1].spread <= 0:
            raise ValueError(f"Nonpositive spread without a previous candle at {bar.timestamp}")
        bars[index] = replace(bar, spread=bars[index - 1].spread)
        repaired.append(bar.timestamp.isoformat())
    return bars, repaired


def initialize(timeframe: str, period: int) -> None:
    spec = TIMEFRAMES[timeframe]
    start, end = spec["periods"][period]
    entry, repaired = load(spec["resolution"], end)
    trend, _ = load("HOUR_4", end)
    STATE.update(timeframe=timeframe, start=start, end=end, entry=entry, trend=trend,
                 repaired=[stamp for stamp in repaired if start.isoformat() <= stamp < end.isoformat()])


def window_for(variant: Variant) -> list:
    lookback = variant.window + 50
    before = [bar for bar in STATE["entry"] if bar.timestamp < STATE["start"]]
    if len(before) < lookback:
        raise ValueError(f"Need {lookback} warm-up candles before {STATE['start']:%Y-%m-%d}")
    # Exactly `lookback` warm-up candles, so the first possible signal is the period's first bar.
    return before[-lookback:] + [
        bar for bar in STATE["entry"] if STATE["start"] <= bar.timestamp < STATE["end"]
    ]


def audit() -> dict:
    data = [bar for bar in STATE["entry"] if STATE["start"] <= bar.timestamp < STATE["end"]]
    return {
        "timeframe": STATE["timeframe"],
        "start_inclusive": STATE["start"].isoformat(), "end_exclusive": STATE["end"].isoformat(),
        "bars": len(data), "first_bar": data[0].timestamp.isoformat(),
        "last_bar": data[-1].timestamp.isoformat(),
        "median_spread": statistics.median(bar.spread for bar in data),
        "repaired_in_period": STATE["repaired"],
        "entry_sha256": hashlib.sha256(
            json.dumps([bar.to_dict() for bar in data], sort_keys=True).encode()).hexdigest(),
    }


def buckets(trades: list) -> list[float]:
    start, end = STATE["start"], STATE["end"]
    step = 3 if TIMEFRAMES[STATE["timeframe"]]["bucket"] == "quarter" else 1
    bounds = []
    cursor = start
    while cursor < end:
        bounds.append(cursor)
        month = cursor.month - 1 + step
        cursor = cursor.replace(year=cursor.year + month // 12, month=month % 12 + 1)
    bounds.append(end)
    return [sum(t.r for t in trades if a <= t.closed_at < b) for a, b in zip(bounds, bounds[1:])]


def bootstrap(trades: list) -> list[float]:
    weeks: dict = defaultdict(lambda: [0.0, 0])
    cursor = STATE["start"].date()
    while cursor < STATE["end"].date():
        weeks[cursor.isocalendar()[:2]]
        cursor += timedelta(days=1)
    for trade in trades:
        bucket = weeks[trade.closed_at.isocalendar()[:2]]
        bucket[0] += trade.r
        bucket[1] += 1
    rng = random.Random(12)
    values = []
    items = list(weeks.values())
    for _ in range(2000):
        draw = rng.choices(items, k=len(items))
        count = sum(b[1] for b in draw)
        if count:
            values.append(sum(b[0] for b in draw) / count)
    values.sort()
    return [values[int(len(values) * q)] for q in (0.025, 0.975)] if values else [0.0, 0.0]


def run(variant: Variant) -> dict:
    config = BacktestConfig(
        resolution=TIMEFRAMES[variant.timeframe]["resolution"],
        trend_resolution=variant.trend,
        management=None,
        risk=RiskConfig(max_positions=1, max_trades_per_day=3),
        lookback=variant.window + 50,
        label=variant.id,
    )
    result = run_backtest(window_for(variant), STATE["trend"] if variant.trend else None, config,
                          strategies=[StructureStrategy(variant.structure)])
    row = result.metrics()
    if row["profit_factor"] == float("inf"):
        row["profit_factor"] = "inf"
    row["variant"] = asdict(variant)
    row["side"] = {
        side: {"trades": sum(1 for t in result.trades if t.direction == side),
               "total_r": sum(t.r for t in result.trades if t.direction == side)}
        for side in ("BUY", "SELL")
    }
    row["buckets"] = buckets(result.trades)
    spreads = {bar.timestamp: bar.spread for bar in STATE["entry"]}
    costs = [spreads[t.opened_at] / abs(t.entry - t.initial_stop) for t in result.trades]
    holds = [(t.closed_at - t.opened_at).total_seconds() / 3600 for t in result.trades]
    row["median_spread_r"] = statistics.median(costs) if costs else 0.0
    row["median_hold_hours"] = statistics.median(holds) if holds else 0.0
    row["extra_cost_average_r"] = (
        sum(t.r - EXTRA_COST_POINTS / abs(t.entry - t.initial_stop) for t in result.trades)
        / len(result.trades) if result.trades else 0.0
    )
    if STATE["start"] == TIMEFRAMES[variant.timeframe]["periods"][1][0]:
        row["week_bootstrap_95_interval"] = bootstrap(result.trades)
    return row


def eligible(row: dict) -> bool:
    need = TIMEFRAMES[row["variant"]["timeframe"]]["min_positive_buckets"]
    return (row["trades"] >= 150 and row["average_r"] > 0.05
            and all(side["total_r"] > 0 for side in row["side"].values())
            and sum(r > 0 for r in row["buckets"]) >= need)


def select(rows: list[dict]) -> list[dict]:
    selected = []
    for family in ("zone", "channel"):
        group = [r for r in rows if r["variant"]["family"] == family]
        allowed = [r for r in group if eligible(r)]
        pool = allowed or [r for r in group if r["trades"] >= 150]
        if pool:
            best = sorted(pool, key=lambda r: (-r["average_r"], r["max_drawdown_r"], r["label"]))[0]
        else:
            best = sorted(group, key=lambda r: (-r["trades"], -r["average_r"], r["label"]))[0]
        selected.append({"variant": best["variant"], "eligible": bool(allowed), "id": best["label"]})
    return selected


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n")


def table(rows: list[dict], timeframe: str) -> str:
    unit = "Q" if TIMEFRAMES[timeframe]["bucket"] == "quarter" else "months"
    lines = [f"# All {len(rows)} training variants ({timeframe} gold structure)", "",
             f"| Variant | N | Avg R | Total R | PF | DD R | Long R | Short R | Positive {unit} | Eligible |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in sorted(rows, key=lambda r: -r["average_r"]):
        pf = r["profit_factor"] if isinstance(r["profit_factor"], str) else f"{r['profit_factor']:.2f}"
        lines.append(
            f"| {r['label']} | {r['trades']} | {r['average_r']:+.3f} | {r['total_r']:+.1f} | {pf} | "
            f"{r['max_drawdown_r']:.1f} | {r['side']['BUY']['total_r']:+.1f} | "
            f"{r['side']['SELL']['total_r']:+.1f} | {sum(q > 0 for q in r['buckets'])}/{len(r['buckets'])} | "
            f"{eligible(r)} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("timeframe", choices=tuple(TIMEFRAMES))
    parser.add_argument("stage", choices=("train", "validate"))
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    out = output(args.timeframe)
    out.mkdir(exist_ok=True)
    if args.stage == "train":
        if (out / "selection.json").exists():
            raise SystemExit("Selection already locked; refusing to overwrite the experiment")
        initialize(args.timeframe, 0)
        metadata = audit()
        with ProcessPoolExecutor(max_workers=args.workers, initializer=initialize,
                                 initargs=(args.timeframe, 0)) as pool:
            rows = list(pool.map(run, variants(args.timeframe)))
        write_json(out / "training.json", {"data": metadata, "rows": rows})
        (out / "TRAINING.md").write_text(table(rows, args.timeframe))
        write_json(out / "selection.json", {
            "created_utc": datetime.now(timezone.utc).isoformat(), "trial_count": len(rows),
            "training_sha256": digest(out / "training.json"), "source_hashes": source_hashes(),
            "selected": select(rows),
        })
        print(table(rows, args.timeframe))
        print("Selections locked. Commit before running validation.")
    else:
        manifest = json.loads((out / "selection.json").read_text())
        if manifest["source_hashes"] != source_hashes():
            raise SystemExit("Sources changed after selection; do not silently retune the holdout")
        if manifest["training_sha256"] != digest(out / "training.json"):
            raise SystemExit("Training results changed after selection")
        if (out / "validation.json").exists():
            raise SystemExit("Holdout already evaluated; refusing an unrecorded repeat")
        initialize(args.timeframe, 1)
        rows = [run(Variant(**choice["variant"])) for choice in manifest["selected"]]
        write_json(out / "validation.json", {"data": audit(), "rows": rows})
        for row in rows:
            print(f"{row['label']} N={row['trades']} avgR={row['average_r']:+.3f}")


if __name__ == "__main__":
    main()
