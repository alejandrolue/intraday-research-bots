"""Issue #60: can the #58 fakeout rules trade daily across four markets? Train, lock, validate."""

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
from goldbot.evolution_strategy import EvolutionConfig, FiveMinuteEvolutionStrategy  # noqa: E402
from goldbot.history import _read  # noqa: E402
from goldbot.risk import RiskConfig  # noqa: E402
from goldbot.sessions import TradingWindow  # noqa: E402

HISTORY = ROOT / "data/history"
OUTPUT = ROOT / "research/results_daily_fakeout"
PROTOCOL = ROOT / "research/DAILY_FAKEOUT_PROTOCOL.md"
MARKETS = ("GOLD", "US100", "US500", "US30")
MAX_SPREAD = {"GOLD": 0.80}
PERIODS = {
    "train": (datetime(2024, 9, 29, tzinfo=timezone.utc), datetime(2026, 9, 29, tzinfo=timezone.utc)),
    "holdout": (datetime(2024, 1, 8, tzinfo=timezone.utc), datetime(2024, 8, 1, tzinfo=timezone.utc)),
}
LOOKBACK = 350
EXTRA_SPREAD_SHARE = 0.5
STATE: dict = {}


@dataclass(frozen=True)
class Variant:
    mode: str
    radius: int
    window: int
    reward: float
    trend: str | None
    hours: str

    @property
    def id(self) -> str:
        return (f"{self.mode}-r{self.radius}-w{self.window}-rr{self.reward:g}-"
                f"{self.trend or 'none'}-{self.hours}")


class FakeoutOnly(FiveMinuteEvolutionStrategy):
    """The #58 strategy with its confluence path switched off."""

    def _confluence(self, candles, zones, current_atr):  # type: ignore[override]
        return None


def variants() -> list[Variant]:
    return [
        Variant(mode, radius, window, reward, trend, hours)
        for mode, radius, window, reward, trend, hours in product(
            ("fakeout", "both"), (3, 6), (144, 288), (1.5, 2.0), (None, "MINUTE_30"),
            ("all", "london-ny"),
        )
    ]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict:
    paths = [Path(__file__), PROTOCOL, ROOT / "src/goldbot/evolution_strategy.py",
             ROOT / "src/goldbot/backtest.py", ROOT / "src/goldbot/risk.py"]
    return {str(path.relative_to(ROOT)): digest(path) for path in paths}


def load(epic: str, resolution: str, end: datetime) -> list:
    indexed = {}
    for path in sorted((HISTORY / epic / resolution).glob("*.csv")):
        for bar in _read(path):
            if bar.timestamp < end:
                indexed[bar.timestamp] = bar
    bars = [indexed[key] for key in sorted(indexed)]
    for index, bar in enumerate(bars):
        if bar.spread <= 0:
            if index == 0 or bars[index - 1].spread <= 0:
                raise ValueError(f"{epic}: nonpositive spread without a previous candle at {bar.timestamp}")
            bars[index] = replace(bar, spread=bars[index - 1].spread)
    return bars


def initialize(period: str) -> None:
    start, end = PERIODS[period]
    STATE.update(period=period, start=start, end=end, data={
        epic: (load(epic, "MINUTE_5", end), load(epic, "MINUTE_30", end)) for epic in MARKETS
    })


def run_market(variant: Variant, epic: str) -> list[dict]:
    entry, trend = STATE["data"][epic]
    start, end = STATE["start"], STATE["end"]
    before = [bar for bar in entry if bar.timestamp < start]
    candles = before[-LOOKBACK:] + [bar for bar in entry if start <= bar.timestamp < end]
    config = BacktestConfig(
        resolution="MINUTE_5",
        trend_resolution=variant.trend,
        window=TradingWindow() if variant.hours == "london-ny" else None,
        management=None,
        risk=RiskConfig(max_positions=1, max_trades_per_day=15,
                        max_spread=MAX_SPREAD.get(epic, 1e9)),
        lookback=LOOKBACK,
        label=f"{variant.id}-{epic}",
    )
    cls = FakeoutOnly if variant.mode == "fakeout" else FiveMinuteEvolutionStrategy
    strategy = cls(EvolutionConfig(window=variant.window, radius=variant.radius,
                                   reward_to_risk=variant.reward))
    result = run_backtest(candles, trend if variant.trend else None, config, strategies=[strategy])
    spreads = {bar.timestamp: bar.spread for bar in entry}
    trades = []
    for trade in result.trades:
        if not start <= trade.opened_at < end:
            continue
        risk = abs(trade.entry - trade.initial_stop)
        extra = EXTRA_SPREAD_SHARE * spreads.get(trade.opened_at, 0.0) / risk if risk else 0.0
        trades.append({
            "market": epic, "direction": trade.direction, "r": trade.r,
            "extra_cost_r": trade.r - extra, "setup": getattr(trade, "strategy", ""),
            "opened_at": trade.opened_at.isoformat(), "closed_at": trade.closed_at.isoformat(),
        })
    return trades


def run(variant: Variant) -> dict:
    trades = sorted(
        (trade for epic in MARKETS for trade in run_market(variant, epic)),
        key=lambda item: item["closed_at"],
    )
    return summarize(variant, trades)


def _drawdown(values: list[float]) -> float:
    peak = equity = worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def summarize(variant: Variant, trades: list[dict]) -> dict:
    start, end = STATE["start"], STATE["end"]
    years = (end - start).days / 365.25
    count = len(trades)
    total = sum(item["r"] for item in trades)
    buckets: dict[str, dict[str, float]] = {"market": defaultdict(float), "side": defaultdict(float),
                                            "setup": defaultdict(float), "month": defaultdict(float)}
    quarters = [0.0] * 8
    for item in trades:
        closed = datetime.fromisoformat(item["closed_at"])
        buckets["market"][item["market"]] += item["r"]
        buckets["side"][item["direction"]] += item["r"]
        buckets["setup"][item["setup"]] += item["r"]
        buckets["month"][closed.strftime("%Y-%m")] += item["r"]
        index = min(7, int((closed - start).days / (365.25 / 4)))
        quarters[index] += item["r"]
    row = {
        "id": variant.id,
        "variant": asdict(variant),
        "trades": count,
        "trades_per_year": count / years,
        "average_r": total / count if count else 0.0,
        "total_r": total,
        "max_drawdown_r": _drawdown([item["r"] for item in trades]),
        "extra_cost_average_r": (sum(item["extra_cost_r"] for item in trades) / count) if count else 0.0,
        "market_r": {epic: buckets["market"].get(epic, 0.0) for epic in MARKETS},
        "market_trades": {epic: sum(item["market"] == epic for item in trades) for epic in MARKETS},
        "side_r": {side: buckets["side"].get(side, 0.0) for side in ("BUY", "SELL")},
        "setup_r": dict(buckets["setup"]),
        "month_r": dict(sorted(buckets["month"].items())),
        "quarter_r": quarters,
    }
    if STATE["period"] == "holdout":
        row["week_bootstrap_95"] = bootstrap(trades)
    return row


def eligible(row: dict) -> bool:
    return (
        row["trades"] >= 500
        and row["average_r"] > 0.05
        and all(value > 0 for value in row["side_r"].values())
        and sum(value > 0 for value in row["quarter_r"]) >= 5
        and sum(value > 0 for value in row["market_r"].values()) >= 3
    )


def holdout_pass(row: dict, training_eligible: bool) -> bool:
    months = [value for value in row["month_r"].values()]
    return (
        training_eligible
        and row["trades"] >= 140
        and row["average_r"] > 0.05
        and sum(value > 0 for value in months) >= 4
        and sum(value >= 0 for value in row["market_r"].values()) >= 3
        and row["extra_cost_average_r"] > 0
    )


def bootstrap(trades: list[dict]) -> list[float]:
    weeks: dict = defaultdict(lambda: [0.0, 0])
    cursor = STATE["start"].date()
    while cursor < STATE["end"].date():
        weeks[cursor.isocalendar()[:2]]
        cursor += timedelta(days=1)
    for item in trades:
        bucket = weeks[datetime.fromisoformat(item["closed_at"]).isocalendar()[:2]]
        bucket[0] += item["r"]
        bucket[1] += 1
    rng = random.Random(60)
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
    pool = allowed or [row for row in rows if row["trades"] >= 500] or rows
    best = sorted(pool, key=lambda row: (-row["average_r"], row["max_drawdown_r"], row["id"]))[0]
    return {"id": best["id"], "variant": best["variant"], "eligible": bool(allowed)}


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def table(rows: list[dict]) -> str:
    lines = [
        f"# All {len(rows)} training variants (fakeout rules on GOLD, US100, US500, US30)",
        "",
        "| Variant | N | N/year | Avg R | Total R | DD R | Long R | Short R | +Q | Markets + | GOLD | US100 | US500 | US30 | Eligible |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in sorted(rows, key=lambda item: -item["average_r"]):
        m = row["market_r"]
        lines.append(
            f"| {row['id']} | {row['trades']} | {row['trades_per_year']:.0f} | "
            f"{row['average_r']:+.3f} | {row['total_r']:+.1f} | {row['max_drawdown_r']:.1f} | "
            f"{row['side_r']['BUY']:+.1f} | {row['side_r']['SELL']:+.1f} | "
            f"{sum(v > 0 for v in row['quarter_r'])}/8 | {sum(v > 0 for v in m.values())}/4 | "
            f"{m['GOLD']:+.1f} | {m['US100']:+.1f} | {m['US500']:+.1f} | {m['US30']:+.1f} | "
            f"{eligible(row)} |"
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
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "trial_count": len(rows),
            "training_sha256": digest(OUTPUT / "training.json"),
            "source_hashes": source_hashes(),
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
        variant = Variant(**choice["variant"])
        row = run(variant)
        row["pass"] = holdout_pass(row, choice["eligible"])
        write_json(OUTPUT / "holdout.json", {"period": PERIODS["holdout"], "row": row})
        print(json.dumps(row, indent=2, default=str))


if __name__ == "__main__":
    main()
