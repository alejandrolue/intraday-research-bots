"""Issue #2: long-only, trend confirmation and ADX regime variants of the 1h bot.

Run from outputs/goldbot with cached history in data/history (fill it with `goldbot backtest`):
    .venv/bin/python research/1h_direction_regime.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from goldbot.backtest import BacktestConfig, run_backtest, with_label  # noqa: E402
from goldbot.history import _read  # noqa: E402
from goldbot.risk import RiskConfig  # noqa: E402

HISTORY = Path(__file__).resolve().parents[1] / "data" / "history" / "GOLD"


def load(resolution: str) -> list:
    candles = []
    for path in sorted((HISTORY / resolution).glob("*.csv")):
        candles.extend(_read(path))
    return candles


BASE = BacktestConfig(
    resolution="HOUR",
    trend_resolution="HOUR_4",
    risk=RiskConfig(max_trades_per_day=15, max_positions=3),
)
VARIANTS = [
    {},
    {"trend_resolution": "DAY"},
    {"directions": "long"},
    {"trend_resolution": "DAY", "directions": "long"},
    {"confirm_trend_resolution": "DAY"},
    {"confirm_trend_resolution": "DAY", "confirm_shorts_only": True},
    {"min_adx": 20.0},
    {"trend_resolution": "DAY", "min_adx": 20.0},
    {"trend_resolution": "DAY", "min_adx": 25.0},
    {"confirm_trend_resolution": "DAY", "confirm_shorts_only": True, "min_adx": 20.0},
]


def run(changes: dict) -> dict:
    config = with_label(BASE, **changes)
    entry = load("HOUR")
    trend = load(config.trend_resolution) if config.trend_resolution else None
    confirm = load(config.confirm_trend_resolution) if config.confirm_trend_resolution else None
    result = run_backtest(entry, trend, config, confirm_candles=confirm)
    metrics = result.metrics()
    side = defaultdict(float)
    quarters = defaultdict(float)
    for trade in result.trades:
        side[trade.direction] += trade.r
        quarters[f"{trade.closed_at.year}Q{(trade.closed_at.month - 1) // 3 + 1}"] += trade.r
    metrics["long_r"] = side["BUY"]
    metrics["short_r"] = side["SELL"]
    metrics["quarters"] = dict(sorted(quarters.items()))
    return metrics


def main() -> None:
    with ProcessPoolExecutor() as pool:
        rows = list(pool.map(run, VARIANTS))
    header = (
        f"{'variant (1h entries, london-ny, managed)':<58} {'trades':>6} {'win%':>5} {'avg R':>7} "
        f"{'total':>7} {'PF':>5} {'maxDD':>6} {'H1':>7} {'H2':>7} {'long':>7} {'short':>7} "
        f"{'neg Q':>5}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        label = row["label"].replace(", hours london-ny, managed", "")
        negative = sum(1 for value in row["quarters"].values() if value < 0)
        print(
            f"{label:<58} {row['trades']:>6} {row['win_rate']:>5.0%} {row['average_r']:>+7.3f} "
            f"{row['total_r']:>+7.1f} {row['profit_factor']:>5.2f} {row['max_drawdown_r']:>6.1f} "
            f"{row['first_half_r']:>+7.1f} {row['second_half_r']:>+7.1f} {row['long_r']:>+7.1f} "
            f"{row['short_r']:>+7.1f} {negative:>2}/{len(row['quarters'])}"
        )
    print()
    for row in rows:
        quarters = " ".join(f"{key}:{value:+.0f}" for key, value in row["quarters"].items())
        print(f"{row['label'].replace(', hours london-ny, managed', ''):<58} {quarters}")


if __name__ == "__main__":
    main()
