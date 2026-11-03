"""Issue #19: out-of-sample exam of the live 1h bot on unseen 2021–2024 GOLD hourly data.

Pre-registered rules are in issue #19. The configuration mirrors the live `1h` session exactly
(see work/goldbot/bots.sh and config defaults); nothing here is tuned.

    python research/1h_out_of_sample.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goldbot.backtest import BacktestConfig, run_backtest, with_label  # noqa: E402
from goldbot.history import _read  # noqa: E402
from goldbot.management import ManagementConfig  # noqa: E402
from goldbot.risk import RiskConfig  # noqa: E402
from goldbot.sessions import TradingWindow  # noqa: E402

HISTORY = ROOT / "data" / "history" / "GOLD"
END = datetime(2024, 9, 15, tzinfo=timezone.utc)  # where the in-sample backtests begin

LIVE = BacktestConfig(
    resolution="HOUR",
    trend_resolution="HOUR_4",
    confirm_trend_resolution="DAY",
    window=TradingWindow(),
    management=ManagementConfig(),
    risk=RiskConfig(max_trades_per_day=15, max_positions=3),
)
DIAGNOSTICS = [
    {"confirm_trend_resolution": None},  # 4h filter only
    {"trend_resolution": "DAY", "confirm_trend_resolution": None},  # daily filter only
]


def load(resolution: str) -> list:
    bars = []
    for path in sorted((HISTORY / resolution).glob("*.csv")):
        bars.extend(bar for bar in _read(path) if bar.timestamp < END)
    return bars


def run(changes: dict) -> dict:
    config = with_label(LIVE, **changes)
    entry = load("HOUR")
    result = run_backtest(
        entry,
        load(config.trend_resolution) if config.trend_resolution else None,
        config,
        confirm_candles=load(config.confirm_trend_resolution)
        if config.confirm_trend_resolution
        else None,
    )
    metrics = result.metrics()
    years: dict[int, float] = defaultdict(float)
    quarters: dict[str, float] = defaultdict(float)
    side: dict[str, list[float]] = defaultdict(list)
    for trade in result.trades:
        years[trade.closed_at.year] += trade.r
        quarters[f"{trade.closed_at.year}Q{(trade.closed_at.month - 1) // 3 + 1}"] += trade.r
        side[trade.direction].append(trade.r)
    metrics.update(
        start=result.start.isoformat() if result.start else None,
        end=result.end.isoformat() if result.end else None,
        years=dict(sorted(years.items())),
        quarters=dict(sorted(quarters.items())),
        side={key: (len(values), sum(values)) for key, values in side.items()},
    )
    return metrics


def checks(row: dict) -> list[tuple[str, bool]]:
    expected = [f"{year}Q{q}" for year in (2021, 2022, 2023, 2024) for q in (1, 2, 3, 4)]
    window = expected[expected.index("2021Q4") : expected.index("2024Q3") + 1]
    positive = sum(row["quarters"].get(quarter, 0.0) > 0 for quarter in window)
    return [
        (f"average R {row['average_r']:+.3f} > +0.05", row["average_r"] > 0.05),
        (f"profit factor {row['profit_factor']:.2f} > 1.10", row["profit_factor"] > 1.10),
        (f"positive quarters {positive}/{len(window)} >= 7", positive >= 7),
        (f"2022 total R {row['years'].get(2022, 0.0):+.1f} >= 0", row["years"].get(2022, 0.0) >= 0),
        (f"max drawdown {row['max_drawdown_r']:.1f}R <= 30", row["max_drawdown_r"] <= 30),
    ]


def main() -> None:
    with ProcessPoolExecutor() as pool:
        rows = list(pool.map(run, [{}] + DIAGNOSTICS))
    live = rows[0]
    print(f"Period: {live['start'][:10]} to {live['end'][:10]} (unseen; in-sample began 2024-09-15)")
    print()
    for row in rows:
        longs, shorts = row["side"].get("BUY", (0, 0.0)), row["side"].get("SELL", (0, 0.0))
        print(
            f"{row['label']}: {row['trades']} trades, {row['win_rate']:.0%} win, "
            f"avg {row['average_r']:+.3f}R, total {row['total_r']:+.1f}R, PF {row['profit_factor']:.2f}, "
            f"max DD {row['max_drawdown_r']:.1f}R | longs {longs[0]} / {longs[1]:+.1f}R, "
            f"shorts {shorts[0]} / {shorts[1]:+.1f}R"
        )
        print("   years: " + ", ".join(f"{y} {r:+.1f}" for y, r in row["years"].items()))
        print("   quarters: " + " ".join(f"{q}:{r:+.0f}" for q, r in row["quarters"].items()))
    print()
    results = checks(live)
    for label, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
    print()
    print("VERDICT (live configuration):", "PASS" if all(ok for _, ok in results) else "FAIL")


if __name__ == "__main__":
    main()
