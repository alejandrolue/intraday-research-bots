"""Issue #69: original vs inverted 5m-learn on GOLD, with and without spread."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goldbot.backtest import BacktestConfig, run_backtest  # noqa: E402
from goldbot.history import _read  # noqa: E402
from goldbot.risk import RiskConfig  # noqa: E402
from goldbot.strategy import DemandZoneStrategy, PullbackStrategy, RetestStrategy  # noqa: E402

HISTORY = ROOT / "data/history/GOLD"
OUTPUT = ROOT / "research/results_invert_5m.json"
U = timezone.utc
PERIODS = {
    "P0": (datetime(2024, 1, 8, tzinfo=U), datetime(2024, 9, 29, tzinfo=U)),
    "Y1": (datetime(2024, 9, 29, tzinfo=U), datetime(2025, 9, 29, tzinfo=U)),
    "Y2": (datetime(2025, 9, 29, tzinfo=U), datetime(2026, 9, 29, tzinfo=U)),
}
VARIANTS = (None, "mirror", "exact", "trend-keep")
WINDOW = 499


def load(resolution: str) -> list:
    bars = {}
    for path in sorted((HISTORY / resolution).glob("*.csv")):
        for bar in _read(path):
            bars[bar.timestamp] = bar
    ordered = [bars[key] for key in sorted(bars)]
    for index, bar in enumerate(ordered):
        if bar.spread <= 0 and index:
            ordered[index] = replace(bar, spread=ordered[index - 1].spread)
    return ordered


ENTRY = load("MINUTE_5")
TREND = load("MINUTE_30")


def run(job: tuple) -> dict:
    invert, zero_spread, period = job
    start, end = PERIODS[period]
    entry = [bar for bar in ENTRY if bar.timestamp < end]
    before = [bar for bar in entry if bar.timestamp < start][-(WINDOW + 10):]
    candles = before + [bar for bar in entry if bar.timestamp >= start]
    trend = [bar for bar in TREND if bar.timestamp < end]
    if zero_spread:
        candles = [replace(bar, spread=0.0) for bar in candles]
    config = BacktestConfig(
        resolution="MINUTE_5", trend_resolution="MINUTE_30", window=None, management=None,
        risk=RiskConfig(max_trades_per_day=50, max_positions=3), lookback=WINDOW,
        trend_lookback=WINDOW, invert=invert,
        label=f"{invert or 'original'}-{'zero' if zero_spread else 'real'}-{period}",
    )
    result = run_backtest(candles, trend, config,
                          strategies=[RetestStrategy(), PullbackStrategy(), DemandZoneStrategy()])
    trades = [t for t in result.trades if start <= t.opened_at < end]
    wins = sum(t.r for t in trades if t.r > 0)
    losses = -sum(t.r for t in trades if t.r < 0)
    years = (end - start).days / 365.25
    return {
        "variant": invert or "original", "spread": "zero" if zero_spread else "real",
        "period": period, "trades": len(trades), "trades_per_year": len(trades) / years,
        "win_rate": sum(t.r > 0 for t in trades) / len(trades) if trades else 0.0,
        "average_r": sum(t.r for t in trades) / len(trades) if trades else 0.0,
        "total_r": sum(t.r for t in trades),
        "profit_factor": wins / losses if losses else None,
        "long_r": sum(t.r for t in trades if t.direction == "BUY"),
        "short_r": sum(t.r for t in trades if t.direction == "SELL"),
    }


def passes(rows: list[dict], variant: str) -> bool:
    mine = {row["period"]: row for row in rows if row["variant"] == variant and row["spread"] == "real"}
    return all(
        mine[p]["average_r"] > 0
        and (mine[p]["profit_factor"] or 0) > 1.1
        and mine[p]["trades"] >= (140 if p == "P0" else 200)
        for p in PERIODS
    )


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit(f"Refusing to overwrite {OUTPUT}")
    jobs = list(product(VARIANTS, (False, True), PERIODS))
    with ProcessPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(run, jobs))
    result = {"rows": rows, "pass": {v: passes(rows, v) for v in ("mirror", "exact", "trend-keep")}}
    OUTPUT.write_text(json.dumps(result, indent=2, default=str) + "\n")
    for row in rows:
        pf = row["profit_factor"]
        print(f"{row['variant']:<11} {row['spread']:<5} {row['period']}  N={row['trades']:5d} "
              f"win={row['win_rate']:.0%} avg={row['average_r']:+.3f} total={row['total_r']:+8.1f} "
              f"PF={pf if pf is None else round(pf, 2)} long={row['long_r']:+.1f} short={row['short_r']:+.1f}")
    print("pass:", result["pass"])


if __name__ == "__main__":
    main()
