"""Issue #85: the #18 opening-range rule as a day-trading rule (flat by the end of the day)."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goldbot.backtest import BacktestConfig, run_backtest  # noqa: E402
from goldbot.history import _read  # noqa: E402
from goldbot.risk import RiskConfig  # noqa: E402
from goldbot.session_strategies import SessionStrategy, SessionVariant  # noqa: E402
from goldbot.sessions import TradingWindow  # noqa: E402

U = timezone.utc
HISTORY = ROOT / "data/history"
OUTPUT = ROOT / "research/results_daytrade_orb.json"
PROTOCOL = ROOT / "research/DAYTRADE_ORB_PROTOCOL.md"
START, END = datetime(2024, 1, 8, tzinfo=U), datetime(2026, 9, 29, tzinfo=U)
MARKETS = {
    "US100": ("nycash", ("America/New_York", 15, 45)),
    "US500": ("nycash", ("America/New_York", 15, 45)),
    "US30": ("nycash", ("America/New_York", 15, 45)),
    "DE40": ("london", ("Europe/Berlin", 17, 15)),
    "UK100": ("london", ("Europe/London", 16, 15)),
}
NEW_MARKETS = ("DE40", "UK100")


def load(epic: str, resolution: str) -> list:
    rows = {}
    for path in sorted((HISTORY / epic / resolution).glob("*.csv")):
        for bar in _read(path):
            if bar.timestamp < END:
                rows[bar.timestamp] = bar
    bars = [rows[k] for k in sorted(rows)]
    for i, bar in enumerate(bars):
        if bar.spread <= 0 and i:
            bars[i] = replace(bar, spread=bars[i - 1].spread)
    return bars


def run(epic: str, *, daily_close: bool, spread_extra: float = 0.0) -> dict:
    session, flat = MARKETS[epic]
    entry, trend = load(epic, "MINUTE_5"), load(epic, "HOUR_4")
    config = BacktestConfig(
        resolution="MINUTE_5", trend_resolution="HOUR_4", window=TradingWindow(), management=None,
        risk=RiskConfig(max_positions=1, max_trades_per_day=3, max_spread=1e9),
        flat_at=flat if daily_close else None, label=f"{epic}-orb-{session}-15-rr2",
    )
    strategy = SessionStrategy(SessionVariant("orb", session, 15, 2.0), start=START)
    result = run_backtest(entry, trend, config, strategies=[strategy])
    spreads = {bar.timestamp: bar.spread for bar in entry}
    trades = [t for t in result.trades if START <= t.opened_at < END]
    rs = []
    years, exits = defaultdict(float), defaultdict(int)
    for t in trades:
        risk = abs(t.entry - t.initial_stop)
        r = t.r - (spread_extra * spreads.get(t.opened_at, 0.0) / risk if risk else 0.0)
        rs.append(r)
        years[str(t.closed_at.year)] += r
        exits[t.exit_reason] += 1
    wins, losses = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    return {"trades": len(rs), "average_r": sum(rs) / len(rs) if rs else 0.0, "total_r": sum(rs),
            "profit_factor": wins / losses if losses else None,
            "win_rate": sum(r > 0 for r in rs) / len(rs) if rs else 0.0,
            "long_r": sum(r for r, t in zip(rs, trades) if t.direction == "BUY"),
            "short_r": sum(r for r, t in zip(rs, trades) if t.direction == "SELL"),
            "years": dict(sorted(years.items())), "exits": dict(exits)}


def passes(row: dict, stressed: dict) -> bool:
    return (row["trades"] >= 150 and row["average_r"] > 0.05 and (row["profit_factor"] or 0) > 1.1
            and sum(v > 0 for v in row["years"].values()) >= 2 and stressed["average_r"] > 0)


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit(f"Refusing to overwrite {OUTPUT}")
    out = {"protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(), "markets": {}}
    for epic in MARKETS:
        row = run(epic, daily_close=True)
        stressed = run(epic, daily_close=True, spread_extra=0.5)
        no_close = run(epic, daily_close=False)
        row.update(stress_extra_spread_average_r=stressed["average_r"], passed=passes(row, stressed),
                   new_market=epic in NEW_MARKETS, without_daily_close=no_close)
        out["markets"][epic] = row
        pf = row["profit_factor"]
        print(f"{epic:6s} {'NEW' if epic in NEW_MARKETS else 're-check':8s} N={row['trades']:4d} win={row['win_rate']:.0%} "
              f"avg={row['average_r']:+.3f} total={row['total_r']:+6.1f} PF={pf if pf is None else round(pf, 2)} "
              f"+50%spread={stressed['average_r']:+.3f} years={ {k: round(v, 1) for k, v in row['years'].items()} } "
              f"exits={row['exits']} long={row['long_r']:+.1f} short={row['short_r']:+.1f} "
              f"| no daily close: N={no_close['trades']} avg={no_close['average_r']:+.3f} | PASS={row['passed']}")
    OUTPUT.write_text(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    main()
