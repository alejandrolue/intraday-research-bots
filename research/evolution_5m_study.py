"""Fixed retrospective comparison for issue #58; no model selection."""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goldbot.backtest import BacktestConfig, run_backtest  # noqa: E402
from goldbot.evolution_strategy import FiveMinuteEvolutionStrategy  # noqa: E402
from goldbot.history import _read  # noqa: E402
from goldbot.risk import RiskConfig  # noqa: E402
from goldbot.strategy import DemandZoneStrategy, PullbackStrategy, RetestStrategy  # noqa: E402


HISTORY = ROOT / "data/history/GOLD"
OUTPUT = ROOT / "research/results_evolution_5m.json"
PROTOCOL = ROOT / "research/EVOLUTION_5M_PROTOCOL.md"
START = datetime(2024, 9, 29, tzinfo=timezone.utc)
SPLIT = START.replace(year=2025)
END = START.replace(year=2026)
EXTRA_COST_POINTS = 0.20


def load(resolution: str, end: datetime) -> tuple[list, list[str]]:
    indexed = {}
    for path in sorted((HISTORY / resolution).glob("*.csv")):
        for bar in _read(path):
            if bar.timestamp < end:
                indexed[bar.timestamp] = bar
    bars = [indexed[key] for key in sorted(indexed)]
    repaired = []
    for index, bar in enumerate(bars):
        if bar.spread > 0:
            continue
        if index == 0 or bars[index - 1].spread <= 0:
            raise ValueError(f"Nonpositive spread without previous candle at {bar.timestamp}")
        bars[index] = replace(bar, spread=bars[index - 1].spread)
        repaired.append(bar.timestamp.isoformat())
    return bars, repaired


def source_hashes() -> dict[str, str]:
    paths = [
        Path(__file__),
        PROTOCOL,
        ROOT / "src/goldbot/evolution_strategy.py",
        ROOT / "src/goldbot/backtest.py",
        ROOT / "src/goldbot/risk.py",
        ROOT / "src/goldbot/paper.py",
    ]
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def summarize(result, entry: list, start: datetime, end: datetime) -> dict:
    row = result.metrics()
    row["side"] = {
        side: {
            "trades": sum(trade.direction == side for trade in result.trades),
            "total_r": sum(trade.r for trade in result.trades if trade.direction == side),
        }
        for side in ("BUY", "SELL")
    }
    quarters: dict[str, float] = defaultdict(float)
    for trade in result.trades:
        quarter = (trade.closed_at.month - 1) // 3 + 1
        quarters[f"{trade.closed_at.year}Q{quarter}"] += trade.r
    row["quarters"] = dict(sorted(quarters.items()))
    spreads = {bar.timestamp: bar.spread for bar in entry}
    row["extra_cost_average_r"] = (
        sum(
            trade.r - EXTRA_COST_POINTS / abs(trade.entry - trade.initial_stop)
            for trade in result.trades
        ) / len(result.trades)
        if result.trades else 0.0
    )
    row["period"] = [start.isoformat(), end.isoformat()]
    row["median_spread"] = statistics.median(
        bar.spread for bar in entry if start <= bar.timestamp < end
    )
    return row


def run(
    label: str,
    strategy_factory,
    max_positions: int,
    cap_daily_risk: bool,
    entry: list,
    trend: list,
    start: datetime,
    end: datetime,
) -> dict:
    lookback = 350
    before = [bar for bar in entry if bar.timestamp < start]
    candles = before[-lookback:] + [bar for bar in entry if start <= bar.timestamp < end]
    config = BacktestConfig(
        resolution="MINUTE_5",
        trend_resolution="MINUTE_30",
        window=None,
        management=None,
        risk=RiskConfig(
            risk_per_trade=0.02,
            max_daily_loss=0.10,
            max_trades_per_day=50,
            max_spread=0.80,
            max_positions=max_positions,
            cap_daily_risk=cap_daily_risk,
        ),
        lookback=350,
        units=100,
        starting_balance=11_000,
        label=label,
    )
    result = run_backtest(candles, trend, config, strategies=strategy_factory())
    return summarize(result, entry, start, end)


def clean(value):
    if isinstance(value, float) and value == float("inf"):
        return "inf"
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean(item) for item in value]
    return value


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit(f"Refusing to overwrite {OUTPUT}")
    entry, repaired = load("MINUTE_5", END)
    trend, _ = load("MINUTE_30", END)
    factories = {
        "baseline_5m_learn": (
            lambda: [RetestStrategy(), PullbackStrategy(), DemandZoneStrategy()], 3, False
        ),
        "5m_evolution": (lambda: [FiveMinuteEvolutionStrategy()], 5, True),
    }
    periods = {"year1": (START, SPLIT), "year2": (SPLIT, END), "combined": (START, END)}
    rows = {
        name: {
            period: run(
                f"{name}-{period}", factory, max_positions, cap_daily_risk,
                entry, trend, start, end
            )
            for period, (start, end) in periods.items()
        }
        for name, (factory, max_positions, cap_daily_risk) in factories.items()
    }
    payload = clean({
        "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
        "source_hashes": source_hashes(),
        "repaired_spreads": repaired,
        "rows": rows,
    })
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    for name, periods in rows.items():
        for period, row in periods.items():
            print(
                f"{name:<20} {period:<8} N={row['trades']:>4} "
                f"avgR={row['average_r']:+.3f} totalR={row['total_r']:+.1f}"
            )


if __name__ == "__main__":
    main()
