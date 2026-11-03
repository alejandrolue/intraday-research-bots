"""Issue #63: train, lock, then validate the 5m-v1 volume pullback grid on GOLD."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
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
from goldbot.market_events import nearest_open  # noqa: E402
from goldbot.risk import RiskConfig  # noqa: E402
from goldbot.sessions import TradingWindow  # noqa: E402
from goldbot.volume_pullback import VolumePullbackConfig, VolumePullbackStrategy  # noqa: E402

HISTORY = ROOT / "data/history/GOLD"
OUTPUT = ROOT / "research/results_volume_pullback"
PROTOCOL = ROOT / "research/VOLUME_PULLBACK_PROTOCOL.md"
PERIODS = {
    "train": (datetime(2024, 9, 29, tzinfo=timezone.utc), datetime(2026, 9, 29, tzinfo=timezone.utc)),
    "holdout": (datetime(2024, 1, 8, tzinfo=timezone.utc), datetime(2024, 9, 1, tzinfo=timezone.utc)),
}
WINDOW = 999
EXTRA_SPREAD_SHARE = 0.5
STATE: dict = {}


@dataclass(frozen=True)
class Variant:
    entry: str
    volume: str
    reward: float
    hours: str

    @property
    def id(self) -> str:
        return f"evp-{self.entry}-{self.volume}-rr{self.reward:g}-{self.hours}"

    @property
    def strategy_name(self) -> str:
        return f"evp-{self.entry}-{self.volume}-rr{self.reward:g}"


def variants() -> list[Variant]:
    return [Variant(*values) for values in product(
        ("pullback", "retest", "breakout", "combined"), ("sma", "rvol", "off"), (2.0, 3.0),
        ("all", "london-ny"),
    )]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict:
    paths = [Path(__file__), PROTOCOL] + [
        ROOT / "src/goldbot" / name
        for name in ("volume_pullback.py", "market_events.py", "backtest.py", "risk.py", "indicators.py")
    ]
    return {str(path.relative_to(ROOT)): digest(path) for path in paths}


def load(resolution: str, end: datetime) -> list:
    indexed = {}
    for path in sorted((HISTORY / resolution).glob("*.csv")):
        for bar in _read(path):
            if bar.timestamp < end:
                indexed[bar.timestamp] = bar
    bars = [indexed[key] for key in sorted(indexed)]
    for index, bar in enumerate(bars):
        if bar.spread <= 0:
            if index == 0 or bars[index - 1].spread <= 0:
                raise ValueError(f"Nonpositive spread without a previous candle at {bar.timestamp}")
            bars[index] = replace(bar, spread=bars[index - 1].spread)
    return bars


def initialize(period: str) -> None:
    start, end = PERIODS[period]
    STATE.update(period=period, start=start, end=end,
                 entry=load("MINUTE_5", end), trend=load("MINUTE_30", end))


def run(variant: Variant) -> dict:
    start, end = STATE["start"], STATE["end"]
    entry = STATE["entry"]
    before = [bar for bar in entry if bar.timestamp < start]
    candles = before[-(WINDOW + 10):] + [bar for bar in entry if start <= bar.timestamp < end]
    config = BacktestConfig(
        resolution="MINUTE_5",
        trend_resolution="MINUTE_30",
        trend_filter="ema21_spec",
        window=TradingWindow() if variant.hours == "london-ny" else None,
        management=None,
        risk=RiskConfig(max_positions=1, max_trades_per_day=6),
        lookback=WINDOW,
        trend_lookback=WINDOW,
        label=variant.id,
    )
    strategy = VolumePullbackStrategy(VolumePullbackConfig.parse(variant.strategy_name))
    result = run_backtest(candles, STATE["trend"], config, strategies=[strategy])
    spreads = {bar.timestamp: bar.spread for bar in entry}
    trades = []
    for trade in result.trades:
        if not start <= trade.opened_at < end:
            continue
        risk = abs(trade.entry - trade.initial_stop)
        extra = EXTRA_SPREAD_SHARE * spreads.get(trade.opened_at, 0.0) / risk if risk else 0.0
        trades.append({"direction": trade.direction, "r": trade.r, "extra_cost_r": trade.r - extra,
                       "opened_at": trade.opened_at, "closed_at": trade.closed_at,
                       "open": nearest_open(trade.opened_at)[0]})
    return summarize(variant, trades)


def _drawdown(values: list[float]) -> float:
    peak = equity = worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def summarize(variant: Variant, trades: list[dict]) -> dict:
    start = STATE["start"]
    trades.sort(key=lambda item: item["closed_at"])
    count = len(trades)
    total = sum(item["r"] for item in trades)
    side: dict[str, float] = defaultdict(float)
    by_open: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    month: dict[str, float] = defaultdict(float)
    quarters = [0.0] * 8
    for item in trades:
        side[item["direction"]] += item["r"]
        by_open[item["open"]][0] += 1
        by_open[item["open"]][1] += item["r"]
        month[item["closed_at"].strftime("%Y-%m")] += item["r"]
        quarters[min(7, int((item["closed_at"] - start).days / (365.25 / 4)))] += item["r"]
    row = {
        "id": variant.id, "variant": asdict(variant), "trades": count,
        "trades_per_year": count / ((STATE["end"] - start).days / 365.25),
        "average_r": total / count if count else 0.0, "total_r": total,
        "max_drawdown_r": _drawdown([item["r"] for item in trades]),
        "extra_cost_average_r": sum(item["extra_cost_r"] for item in trades) / count if count else 0.0,
        "side_r": {key: side.get(key, 0.0) for key in ("BUY", "SELL")},
        "open_split": {key: {"trades": value[0], "total_r": value[1]} for key, value in by_open.items()},
        "month_r": dict(sorted(month.items())), "quarter_r": quarters,
    }
    if STATE["period"] == "holdout":
        row["week_bootstrap_95"] = bootstrap(trades)
    return row


def eligible(row: dict) -> bool:
    return (row["trades"] >= 150 and row["average_r"] > 0.05
            and all(value > 0 for value in row["side_r"].values())
            and sum(value > 0 for value in row["quarter_r"]) >= 5)


def holdout_pass(row: dict, training_eligible: bool) -> bool:
    return (training_eligible and row["trades"] >= 50 and row["average_r"] > 0.05
            and sum(value > 0 for value in row["month_r"].values()) >= 4
            and row["extra_cost_average_r"] > 0)


def bootstrap(trades: list[dict]) -> list[float]:
    weeks: dict = defaultdict(lambda: [0.0, 0])
    cursor = STATE["start"].date()
    while cursor < STATE["end"].date():
        weeks[cursor.isocalendar()[:2]]
        cursor += timedelta(days=1)
    for item in trades:
        bucket = weeks[item["closed_at"].isocalendar()[:2]]
        bucket[0] += item["r"]
        bucket[1] += 1
    rng = random.Random(63)
    items = list(weeks.values())
    values = []
    for _ in range(2000):
        draw = rng.choices(items, k=len(items))
        count = sum(item[1] for item in draw)
        if count:
            values.append(sum(item[0] for item in draw) / count)
    values.sort()
    return [values[int(len(values) * q)] for q in (0.025, 0.975)] if values else [0.0, 0.0]


def select(rows: list[dict]) -> dict:
    allowed = [row for row in rows if eligible(row)]
    pool = allowed or [row for row in rows if row["trades"] >= 150]
    if pool:
        best = sorted(pool, key=lambda row: (-row["average_r"], row["max_drawdown_r"], row["id"]))[0]
    else:
        best = sorted(rows, key=lambda row: (-row["trades"], row["id"]))[0]
    return {"id": best["id"], "variant": best["variant"], "eligible": bool(allowed)}


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def table(rows: list[dict]) -> str:
    lines = [
        f"# All {len(rows)} training variants (5m-v1 volume pullback, GOLD)",
        "",
        "| Variant | N | N/year | Avg R | Total R | DD R | Long R | Short R | +Q | +50% spread | Eligible |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in sorted(rows, key=lambda item: -item["average_r"]):
        lines.append(
            f"| {row['id']} | {row['trades']} | {row['trades_per_year']:.0f} | {row['average_r']:+.3f} | "
            f"{row['total_r']:+.1f} | {row['max_drawdown_r']:.1f} | {row['side_r']['BUY']:+.1f} | "
            f"{row['side_r']['SELL']:+.1f} | {sum(v > 0 for v in row['quarter_r'])}/8 | "
            f"{row['extra_cost_average_r']:+.3f} | {eligible(row)} |"
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
        with ProcessPoolExecutor(max_workers=args.workers, initializer=initialize,
                                 initargs=("train",)) as pool:
            rows = list(pool.map(run, variants()))
        write_json(OUTPUT / "training.json", {"period": PERIODS["train"], "rows": rows})
        (OUTPUT / "TRAINING.md").write_text(table(rows))
        write_json(OUTPUT / "selection.json", {
            "created_utc": datetime.now(timezone.utc).isoformat(), "trial_count": len(rows),
            "training_sha256": digest(OUTPUT / "training.json"), "source_hashes": source_hashes(),
            "selected": select(rows),
        })
        print(table(rows))
        print("Selection locked. Commit before running validation.")
    else:
        manifest = json.loads((OUTPUT / "selection.json").read_text())
        if manifest["source_hashes"] != source_hashes():
            raise SystemExit("Sources changed after selection; do not silently retune the holdout")
        if manifest["training_sha256"] != digest(OUTPUT / "training.json"):
            raise SystemExit("Training results changed after selection")
        if (OUTPUT / "holdout.json").exists():
            raise SystemExit("Holdout already evaluated; refusing an unrecorded repeat")
        initialize("holdout")
        choice = manifest["selected"]
        row = run(Variant(**choice["variant"]))
        row["pass"] = holdout_pass(row, choice["eligible"])
        write_json(OUTPUT / "holdout.json", {"period": PERIODS["holdout"], "row": row})
        print(json.dumps(row, indent=2, default=str))


if __name__ == "__main__":
    main()
