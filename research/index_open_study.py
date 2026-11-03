"""Issue #18: train, freeze, then validate the US index cash-open grid (INDEX_OPEN_PROTOCOL.md).

Same design as Codex's `five_minute_study.py`, generalised to several markets. Run from
outputs/goldbot with the live venv:
    python research/index_open_study.py train
    # commit the training outputs and selection before opening the holdout
    python research/index_open_study.py validate
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
from goldbot.session_strategies import SessionStrategy, SessionVariant  # noqa: E402

HISTORY = ROOT / "data" / "history"
OUTPUT = ROOT / "research" / "results_index_open"
MARKETS = ("US100", "US30")
START = datetime(2024, 9, 29, tzinfo=timezone.utc)
SPLIT = START.replace(year=2025)
END = START.replace(year=2026)
EXTRA_COST_POINTS = 1.0
DATA: dict[str, tuple[list, list]] = {}
REPAIRED: dict[str, list[str]] = {}
PERIOD = (START, SPLIT)


@dataclass(frozen=True)
class Variant:
    market: str
    family: str
    minutes: int
    reward: float
    trend: str | None

    @property
    def id(self) -> str:
        return (
            f"{self.market}-{self.family}-nycash-{self.minutes}-rr{self.reward:g}-"
            f"{self.trend or 'none'}"
        )

    def strategy(self, start: datetime) -> SessionStrategy:
        return SessionStrategy(
            SessionVariant(self.family, "nycash", self.minutes, self.reward), start=start
        )


def variants() -> list[Variant]:
    return [
        Variant(*values)
        for values in product(
            MARKETS, ("orb", "rolling_sweep"), (15, 30), (1.6, 2.0), (None, "HOUR_4")
        )
    ]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict:
    paths = [Path(__file__), ROOT / "research/INDEX_OPEN_PROTOCOL.md"]
    paths += sorted((ROOT / "src/goldbot").glob("*.py"))
    return {str(path.relative_to(ROOT)): digest(path) for path in paths}


def load(market: str, resolution: str, end: datetime) -> list:
    indexed = {}
    for path in sorted((HISTORY / market / resolution).glob("*.csv")):
        if path.stem > end.strftime("%Y-%m"):
            continue
        for bar in _read(path):
            if bar.timestamp < end:
                if bar.timestamp in indexed and indexed[bar.timestamp] != bar:
                    raise ValueError(f"Conflicting cached {market} candles at {bar.timestamp}")
                indexed[bar.timestamp] = bar
    if not indexed:
        raise ValueError(f"No cached {market} {resolution} data")
    bars = [indexed[key] for key in sorted(indexed)]
    repaired = []
    for index, bar in enumerate(bars):  # protocol amendment A1 from issue #12
        if bar.spread > 0:
            continue
        if index == 0 or bars[index - 1].spread <= 0:
            raise ValueError(f"Nonpositive {market} spread without a previous candle at {bar.timestamp}")
        bars[index] = replace(bar, spread=bars[index - 1].spread)
        repaired.append(bar.timestamp.isoformat())
    REPAIRED[f"{market}/{resolution}"] = repaired
    return bars


def initialize(start: datetime, end: datetime) -> None:
    global PERIOD
    for market in MARKETS:
        entry = load(market, "MINUTE_5", end)
        before = [bar for bar in entry if bar.timestamp < start]
        if len(before) < 300:
            raise ValueError(f"Need 300 warm-up bars for {market}")
        DATA[market] = (
            before[-300:] + [bar for bar in entry if start <= bar.timestamp < end],
            load(market, "HOUR_4", end),
        )
    PERIOD = (start, end)


def audit() -> dict:
    report = {"start_inclusive": PERIOD[0].isoformat(), "end_exclusive": PERIOD[1].isoformat()}
    for market, (entry, trend) in DATA.items():
        data = entry[300:]
        report[market] = {
            "bars": len(data),
            "first_bar": data[0].timestamp.isoformat(),
            "last_bar": data[-1].timestamp.isoformat(),
            "median_spread": statistics.median(bar.spread for bar in data),
            "max_spread": max(bar.spread for bar in data),
            "repaired_in_period": [
                stamp for stamp in REPAIRED.get(f"{market}/MINUTE_5", [])
                if PERIOD[0].isoformat() <= stamp < PERIOD[1].isoformat()
            ],
            "entry_sha256": hashlib.sha256(
                json.dumps([bar.to_dict() for bar in entry], sort_keys=True).encode()
            ).hexdigest(),
        }
    return report


def quarters(start: datetime) -> list[datetime]:
    return [start, start.replace(month=12), start.replace(year=start.year + 1, month=3),
            start.replace(year=start.year + 1, month=6), start.replace(year=start.year + 1)]


def bootstrap(trades: list) -> list[float]:
    weeks: dict = defaultdict(lambda: [0.0, 0])
    cursor = PERIOD[0].date()
    while cursor < PERIOD[1].date():
        weeks[cursor.isocalendar()[:2]]
        cursor += timedelta(days=1)
    for trade in trades:
        bucket = weeks[trade.closed_at.isocalendar()[:2]]
        bucket[0] += trade.r
        bucket[1] += 1
    buckets = list(weeks.values())
    rng = random.Random(12)
    values = []
    for _ in range(2000):
        draw = rng.choices(buckets, k=len(buckets))
        count = sum(bucket[1] for bucket in draw)
        if count:
            values.append(sum(bucket[0] for bucket in draw) / count)
    values.sort()
    return [values[int(len(values) * q)] for q in (0.025, 0.975)] if values else [0.0, 0.0]


def run(variant: Variant) -> dict:
    entry, trend = DATA[variant.market]
    config = BacktestConfig(
        resolution="MINUTE_5",
        trend_resolution=variant.trend,
        management=None,
        risk=RiskConfig(max_positions=1, max_trades_per_day=3, max_spread=1e9),
        label=variant.id,
    )
    result = run_backtest(
        entry, trend if variant.trend else None, config, strategies=[variant.strategy(PERIOD[0])]
    )
    row = result.metrics()
    row["variant"] = asdict(variant)
    row["side"] = {
        side: {
            "trades": sum(1 for t in result.trades if t.direction == side),
            "total_r": sum(t.r for t in result.trades if t.direction == side),
        }
        for side in ("BUY", "SELL")
    }
    bounds = quarters(PERIOD[0])
    row["quarters"] = [
        sum(t.r for t in result.trades if a <= t.closed_at < b) for a, b in zip(bounds, bounds[1:])
    ]
    spreads = {bar.timestamp: bar.spread for bar in entry}
    costs = [spreads[t.opened_at] / abs(t.entry - t.initial_stop) for t in result.trades]
    holds = [(t.closed_at - t.opened_at).total_seconds() / 3600 for t in result.trades]
    row["median_spread_r"] = statistics.median(costs) if costs else 0.0
    row["median_hold_hours"] = statistics.median(holds) if holds else 0.0
    row["max_hold_hours"] = max(holds, default=0.0)
    row["overnight_utc"] = sum(t.closed_at.date() != t.opened_at.date() for t in result.trades)
    row["extra_cost_average_r"] = (
        sum(t.r - EXTRA_COST_POINTS / abs(t.entry - t.initial_stop) for t in result.trades)
        / len(result.trades)
        if result.trades
        else 0.0
    )
    if PERIOD[0] == SPLIT:
        row["week_bootstrap_95_interval"] = bootstrap(result.trades)
    return row


def eligible(row: dict) -> bool:
    return (row["trades"] >= 150 and row["average_r"] > 0.05
            and all(side["total_r"] > 0 for side in row["side"].values())
            and sum(r > 0 for r in row["quarters"]) >= 3)


def select(rows: list[dict]) -> list[dict]:
    selected = []
    for market in MARKETS:
        for family in ("orb", "rolling_sweep"):
            group = [r for r in rows if r["variant"]["market"] == market and r["variant"]["family"] == family]
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


def clean(row: dict) -> dict:
    # Profit factor is infinite when a variant has no losing trades; JSON cannot hold inf.
    if row["profit_factor"] == float("inf"):
        row = {**row, "profit_factor": "inf"}
    return row


def training_table(rows: list[dict]) -> str:
    lines = [
        "# All 48 year-1 variants (US index cash open)", "",
        "| Variant | N | Avg R | Total R | PF | DD R | Long R | Short R | Positive Q | Eligible |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in sorted(rows, key=lambda r: -r["average_r"]):
        lines.append(
            f"| {r['label']} | {r['trades']} | {r['average_r']:+.3f} | {r['total_r']:+.1f} | "
            f"{r['profit_factor'] if isinstance(r['profit_factor'], str) else format(r['profit_factor'], '.2f')} | "
            f"{r['max_drawdown_r']:.1f} | {r['side']['BUY']['total_r']:+.1f} | "
            f"{r['side']['SELL']['total_r']:+.1f} | {sum(q > 0 for q in r['quarters'])}/4 | {eligible(r)} |"
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
        initialize(START, SPLIT)
        metadata = audit()
        with ProcessPoolExecutor(max_workers=args.workers, initializer=initialize,
                                 initargs=(START, SPLIT)) as pool:
            rows = [clean(row) for row in pool.map(run, variants())]
        write_json(OUTPUT / "training.json", {"data": metadata, "rows": rows})
        (OUTPUT / "TRAINING.md").write_text(training_table(rows))
        write_json(OUTPUT / "selection.json", {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "trial_count": len(rows),
            "training_sha256": digest(OUTPUT / "training.json"),
            "source_hashes": source_hashes(),
            "selected": select(rows),
        })
        print(training_table(rows))
        print("Selections locked. Commit before running validation.")
    else:
        manifest = json.loads((OUTPUT / "selection.json").read_text())
        if manifest["source_hashes"] != source_hashes():
            raise SystemExit("Sources changed after selection; do not silently retune the holdout")
        if manifest["training_sha256"] != digest(OUTPUT / "training.json"):
            raise SystemExit("Training results changed after selection")
        if (OUTPUT / "validation.json").exists():
            raise SystemExit("Holdout already evaluated; refusing an unrecorded repeat")
        initialize(SPLIT, END)
        rows = [clean(run(Variant(**choice["variant"]))) for choice in manifest["selected"]]
        write_json(OUTPUT / "validation.json", {"data": audit(), "rows": rows})
        for row in rows:
            print(f"{row['label']} N={row['trades']} avgR={row['average_r']:+.3f}")


if __name__ == "__main__":
    main()
