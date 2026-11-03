"""Issue #31: train, freeze, then validate the Flux Charts ORB grid (FLUX_ORB_PROTOCOL.md).

Run from outputs/goldbot with the live venv, once per timeframe:
    python research/orb_flux_study.py 5m train
    # commit the training outputs and selection before opening the holdout
    python research/orb_flux_study.py 5m validate
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
from goldbot.session_strategies import SessionStrategy, SessionVariant  # noqa: E402

HISTORY = ROOT / "data" / "history"
MARKETS = ("US500", "US100")
EXTRA_COST_POINTS = {"US500": 0.25, "US100": 1.0}


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


TIMEFRAMES = {
    "5m": {
        "resolution": "MINUTE_5",
        "bar_minutes": 5,
        "min_trades": 150,
        "pass_trades": 300,
        "periods": ((utc(2024, 9, 29), utc(2025, 9, 29)), (utc(2025, 9, 29), utc(2026, 9, 29))),
        "bucket": "quarter",
        "min_positive_buckets": 3,     # of 4 quarters (training)
        "pass_positive_buckets": 5,    # of 8 quarters (both periods)
    },
    "1m": {
        "resolution": "MINUTE",
        "bar_minutes": 1,
        "min_trades": 75,
        "pass_trades": 150,
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
    market: str
    family: str
    minutes: int
    reward: float
    bias: str | None

    @property
    def session(self) -> SessionVariant:
        return SessionVariant(self.family, "nycash", self.minutes, self.reward)

    @property
    def id(self) -> str:
        return f"{self.timeframe}-{self.market}-{self.session.id}-{self.bias or 'none'}"


def variants(timeframe: str) -> list[Variant]:
    return [
        Variant(timeframe, *values)
        for values in product(MARKETS, ("orbside", "orbmid"), (5, 15, 30, 60), (1.0, 1.5, 2.0),
                              (None, "DAY"))
    ]


def output(timeframe: str) -> Path:
    return ROOT / "research" / f"results_flux_orb_{timeframe}"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict:
    paths = [Path(__file__), ROOT / "research/FLUX_ORB_PROTOCOL.md"]
    paths += sorted((ROOT / "src/goldbot").glob("*.py"))
    return {str(path.relative_to(ROOT)): digest(path) for path in paths}


def load(market: str, resolution: str, end: datetime) -> tuple[list, list[str]]:
    indexed = {}
    for path in sorted((HISTORY / market / resolution).glob("*.csv")):
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


LOOKBACK = 300


def initialize(timeframe: str, period: int) -> None:
    spec = TIMEFRAMES[timeframe]
    start, end = spec["periods"][period]
    data, repaired = {}, {}
    for market in MARKETS:
        entry, fixes = load(market, spec["resolution"], end)
        before = [bar for bar in entry if bar.timestamp < start]
        if len(before) < LOOKBACK:
            raise ValueError(f"Need {LOOKBACK} warm-up candles for {market}")
        # Exactly LOOKBACK warm-up candles, so the first possible signal is the period's first bar.
        data[market] = (before[-LOOKBACK:] + [b for b in entry if start <= b.timestamp < end],
                        load(market, "DAY", end)[0])
        repaired[market] = [s for s in fixes if start.isoformat() <= s < end.isoformat()]
    STATE.update(timeframe=timeframe, start=start, end=end, data=data, repaired=repaired)


def audit() -> dict:
    report = {"timeframe": STATE["timeframe"], "start_inclusive": STATE["start"].isoformat(),
              "end_exclusive": STATE["end"].isoformat()}
    for market, (entry, _) in STATE["data"].items():
        data = entry[LOOKBACK:]
        report[market] = {
            "bars": len(data), "first_bar": data[0].timestamp.isoformat(),
            "last_bar": data[-1].timestamp.isoformat(),
            "median_spread": statistics.median(bar.spread for bar in data),
            "repaired_in_period": STATE["repaired"][market],
            "entry_sha256": hashlib.sha256(
                json.dumps([bar.to_dict() for bar in data], sort_keys=True).encode()).hexdigest(),
        }
    return report


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
    spec = TIMEFRAMES[variant.timeframe]
    entry, daily = STATE["data"][variant.market]
    config = BacktestConfig(
        resolution=spec["resolution"],
        trend_resolution=variant.bias,
        management=None,
        risk=RiskConfig(max_positions=1, max_trades_per_day=1, max_spread=1e9,
                        minimum_reward_to_risk=1.0),
        lookback=LOOKBACK,
        label=variant.id,
    )
    strategy = SessionStrategy(variant.session, start=STATE["start"], bar_minutes=spec["bar_minutes"])
    result = run_backtest(entry, daily if variant.bias else None, config, strategies=[strategy])
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
    spreads = {bar.timestamp: bar.spread for bar in entry}
    costs = [spreads[t.opened_at] / abs(t.entry - t.initial_stop) for t in result.trades]
    holds = [(t.closed_at - t.opened_at).total_seconds() / 3600 for t in result.trades]
    extra = EXTRA_COST_POINTS[variant.market]
    row["median_spread_r"] = statistics.median(costs) if costs else 0.0
    row["median_hold_hours"] = statistics.median(holds) if holds else 0.0
    row["extra_cost_average_r"] = (
        sum(t.r - extra / abs(t.entry - t.initial_stop) for t in result.trades) / len(result.trades)
        if result.trades else 0.0
    )
    if STATE["start"] == spec["periods"][1][0]:
        row["week_bootstrap_95_interval"] = bootstrap(result.trades)
    return row


def eligible(row: dict) -> bool:
    spec = TIMEFRAMES[row["variant"]["timeframe"]]
    need = spec["min_positive_buckets"]
    return (row["trades"] >= spec["min_trades"] and row["average_r"] > 0.05
            and all(side["total_r"] > 0 for side in row["side"].values())
            and sum(r > 0 for r in row["buckets"]) >= need)


def select(rows: list[dict]) -> list[dict]:
    selected = []
    for market in MARKETS:
        for family in ("orbside", "orbmid"):
            group = [r for r in rows if r["variant"]["market"] == market and r["variant"]["family"] == family]
            minimum = TIMEFRAMES[group[0]["variant"]["timeframe"]]["min_trades"]
            allowed = [r for r in group if eligible(r)]
            pool = allowed or [r for r in group if r["trades"] >= minimum]
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
    lines = [f"# All {len(rows)} training variants ({timeframe} Flux Charts ORB)", "",
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
