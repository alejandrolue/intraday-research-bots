"""Issue #56: train, freeze, then validate the 5m Gold zone-decision grid."""

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
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goldbot.backtest import BacktestConfig, run_backtest  # noqa: E402
from goldbot.history import _read  # noqa: E402
from goldbot.risk import RiskConfig  # noqa: E402
from goldbot.sessions import TradingWindow  # noqa: E402
from zone_decision_strategies import Variant, ZoneDecisionStrategy  # noqa: E402

HISTORY = ROOT / "data/history/GOLD"
OUTPUT = ROOT / "research/results_zone_decision_5m"
PROTOCOL = ROOT / "research/ZONE_DECISION_PROTOCOL.md"
START = datetime(2024, 9, 29, tzinfo=timezone.utc)
SPLIT = START.replace(year=2025)
END = START.replace(year=2026)
EXTRA_COST_POINTS = 0.20
STATE: dict = {}


def variants() -> list[Variant]:
    rows = []
    shared = product((288, 864), (3, 6), (None, "HOUR_4"), ("all", "london-ny"))
    for window, radius, trend, hours in shared:
        for room in (1.0, 1.5):
            rows.append(Variant("ema_room", window, radius, room, trend, hours))
        for family in ("rejection", "breakout_retest"):
            for reward in (1.5, 2.0):
                rows.append(Variant(family, window, radius, reward, trend, hours))
    return rows


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict:
    paths = [Path(__file__), ROOT / "research/zone_decision_strategies.py", PROTOCOL]
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
    for index, bar in enumerate(bars):
        if bar.spread > 0:
            continue
        if index == 0 or bars[index - 1].spread <= 0:
            raise ValueError(f"Nonpositive spread without a previous candle at {bar.timestamp}")
        bars[index] = replace(bar, spread=bars[index - 1].spread)
        repaired.append(bar.timestamp.isoformat())
    return bars, repaired


def initialize(period: int) -> None:
    start, end = ((START, SPLIT), (SPLIT, END))[period]
    entry, repaired = load("MINUTE_5", end)
    trend, _ = load("HOUR_4", end)
    STATE.update(start=start, end=end, entry=entry, trend=trend, repaired=repaired)


def window_for(variant: Variant) -> list:
    lookback = variant.window + 50
    before = [bar for bar in STATE["entry"] if bar.timestamp < STATE["start"]]
    if len(before) < lookback:
        raise ValueError(f"Need {lookback} warm-up candles")
    return before[-lookback:] + [
        bar for bar in STATE["entry"] if STATE["start"] <= bar.timestamp < STATE["end"]
    ]


def audit() -> dict:
    data = [bar for bar in STATE["entry"] if STATE["start"] <= bar.timestamp < STATE["end"]]
    return {
        "start_inclusive": STATE["start"].isoformat(),
        "end_exclusive": STATE["end"].isoformat(),
        "bars": len(data),
        "first_bar": data[0].timestamp.isoformat(),
        "last_bar": data[-1].timestamp.isoformat(),
        "median_spread": statistics.median(bar.spread for bar in data),
        "repaired_in_period": [
            stamp for stamp in STATE["repaired"]
            if STATE["start"].isoformat() <= stamp < STATE["end"].isoformat()
        ],
        "entry_sha256": hashlib.sha256(
            json.dumps([bar.to_dict() for bar in data], sort_keys=True).encode()
        ).hexdigest(),
    }


def quarters(trades: list) -> list[float]:
    bounds = [
        STATE["start"],
        STATE["start"].replace(month=12),
        STATE["start"].replace(year=STATE["start"].year + 1, month=3),
        STATE["start"].replace(year=STATE["start"].year + 1, month=6),
        STATE["end"],
    ]
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
    rng = random.Random(56)
    items = list(weeks.values())
    values = []
    for _ in range(2000):
        draw = rng.choices(items, k=len(items))
        count = sum(item[1] for item in draw)
        if count:
            values.append(sum(item[0] for item in draw) / count)
    values.sort()
    return [values[int(len(values) * q)] for q in (0.025, 0.975)] if values else [0.0, 0.0]


def run(variant: Variant) -> dict:
    config = BacktestConfig(
        resolution="MINUTE_5",
        trend_resolution=variant.trend,
        window=None if variant.hours == "all" else TradingWindow(),
        management=None,
        risk=RiskConfig(max_positions=1, max_trades_per_day=15),
        lookback=variant.window + 50,
        label=variant.id,
    )
    result = run_backtest(
        window_for(variant),
        STATE["trend"] if variant.trend else None,
        config,
        strategies=[ZoneDecisionStrategy(variant)],
    )
    row = result.metrics()
    if row["profit_factor"] == float("inf"):
        row["profit_factor"] = "inf"
    row["variant"] = asdict(variant)
    row["side"] = {
        side: {
            "trades": sum(1 for trade in result.trades if trade.direction == side),
            "total_r": sum(trade.r for trade in result.trades if trade.direction == side),
        }
        for side in ("BUY", "SELL")
    }
    row["quarters"] = quarters(result.trades)
    spreads = {bar.timestamp: bar.spread for bar in STATE["entry"]}
    costs = [spreads[t.opened_at] / abs(t.entry - t.initial_stop) for t in result.trades]
    row["median_spread_r"] = statistics.median(costs) if costs else 0.0
    row["extra_cost_average_r"] = (
        sum(t.r - EXTRA_COST_POINTS / abs(t.entry - t.initial_stop) for t in result.trades)
        / len(result.trades) if result.trades else 0.0
    )
    if STATE["start"] == SPLIT:
        row["week_bootstrap_95_interval"] = bootstrap(result.trades)
    return row


def eligible(row: dict) -> bool:
    return (
        row["trades"] >= 150
        and row["average_r"] > 0.05
        and all(side["total_r"] > 0 for side in row["side"].values())
        and sum(value > 0 for value in row["quarters"]) >= 3
    )


def select(rows: list[dict]) -> list[dict]:
    selections = []
    for family in ("ema_room", "rejection", "breakout_retest"):
        group = [row for row in rows if row["variant"]["family"] == family]
        allowed = [row for row in group if eligible(row)]
        pool = allowed or [row for row in group if row["trades"] >= 150]
        best = sorted(
            pool or group,
            key=lambda row: (-row["average_r"], row["max_drawdown_r"], row["label"]),
        )[0]
        selections.append({"variant": best["variant"], "eligible": bool(allowed), "id": best["label"]})
    return selections


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n")


def table(rows: list[dict]) -> str:
    lines = [
        f"# All {len(rows)} training variants (5m Gold zone decisions)",
        "",
        "| Variant | N | Avg R | Total R | PF | DD R | Long R | Short R | Positive Q | Eligible |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in sorted(rows, key=lambda item: -item["average_r"]):
        pf = row["profit_factor"] if isinstance(row["profit_factor"], str) else f'{row["profit_factor"]:.2f}'
        lines.append(
            f"| {row['label']} | {row['trades']} | {row['average_r']:+.3f} | "
            f"{row['total_r']:+.1f} | {pf} | {row['max_drawdown_r']:.1f} | "
            f"{row['side']['BUY']['total_r']:+.1f} | {row['side']['SELL']['total_r']:+.1f} | "
            f"{sum(value > 0 for value in row['quarters'])}/4 | {eligible(row)} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("train", "validate"))
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    OUTPUT.mkdir(exist_ok=True)
    if args.stage == "train":
        if (OUTPUT / "selection.json").exists():
            raise SystemExit("Selection already locked; refusing to overwrite the experiment")
        initialize(0)
        metadata = audit()
        with ProcessPoolExecutor(max_workers=args.workers, initializer=initialize, initargs=(0,)) as pool:
            rows = list(pool.map(run, variants()))
        write_json(OUTPUT / "training.json", {"data": metadata, "rows": rows})
        (OUTPUT / "TRAINING.md").write_text(table(rows))
        write_json(OUTPUT / "selection.json", {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "trial_count": len(rows),
            "training_sha256": digest(OUTPUT / "training.json"),
            "source_hashes": source_hashes(),
            "selected": select(rows),
        })
        print(table(rows))
        print("Selections locked. Commit before running validation.")
    else:
        manifest = json.loads((OUTPUT / "selection.json").read_text())
        if manifest["source_hashes"] != source_hashes():
            raise SystemExit("Sources changed after selection; do not silently retune the holdout")
        if manifest["training_sha256"] != digest(OUTPUT / "training.json"):
            raise SystemExit("Training results changed after selection")
        if (OUTPUT / "validation.json").exists():
            raise SystemExit("Holdout already evaluated; refusing an unrecorded repeat")
        initialize(1)
        rows = [run(Variant(**choice["variant"])) for choice in manifest["selected"]]
        write_json(OUTPUT / "validation.json", {"data": audit(), "rows": rows})
        for row in rows:
            print(f"{row['label']} N={row['trades']} avgR={row['average_r']:+.3f}")


if __name__ == "__main__":
    main()
