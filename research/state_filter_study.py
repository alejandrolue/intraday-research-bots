"""Issue #81: discover, lock and validate a market-state filter for the 5m-learn signals."""

from __future__ import annotations

import argparse
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
from goldbot.models import MarketSetup  # noqa: E402
from goldbot.regime import market_regime  # noqa: E402
from goldbot.risk import RiskConfig  # noqa: E402
from goldbot.strategy import DemandZoneStrategy, PullbackStrategy, RetestStrategy  # noqa: E402

U = timezone.utc
HISTORY = ROOT / "data/history/GOLD"
OUTPUT = ROOT / "research/results_state_filter"
PROTOCOL = ROOT / "research/STATE_FILTER_PROTOCOL.md"
DISCOVERY = (datetime(2024, 1, 8, tzinfo=U), datetime(2025, 9, 29, tzinfo=U))
VALIDATION = (datetime(2025, 9, 29, tzinfo=U), datetime(2026, 9, 29, tzinfo=U))
WINDOW = 499
DIMENSIONS = ("session", "adx", "volatility", "efficiency", "state", "range")


def load(resolution: str) -> list:
    rows = {}
    for path in sorted((HISTORY / resolution).glob("*.csv")):
        for bar in _read(path):
            rows[bar.timestamp] = bar
    bars = [rows[key] for key in sorted(rows)]
    for index, bar in enumerate(bars):
        if bar.spread <= 0 and index:
            bars[index] = replace(bar, spread=bars[index - 1].spread)
    return bars


ENTRY, TREND = load("MINUTE_5"), load("MINUTE_30")
INDEX = {bar.timestamp: i for i, bar in enumerate(ENTRY)}
_STATE_CACHE: dict = {}


def regime_at(signal_time: datetime) -> dict:
    if signal_time not in _STATE_CACHE:
        i = INDEX[signal_time]
        _STATE_CACHE[signal_time] = market_regime(ENTRY[max(0, i - WINDOW + 1):i + 1])
    return _STATE_CACHE[signal_time]


def tags(regime: dict, direction: str) -> dict | None:
    if not regime:
        return None
    long = direction == "BUY"
    label = regime["label"]
    if label in ("trend_up", "trend_down", "breakout_up", "breakout_down"):
        kind, side = label.split("_")
        state = f"{kind}_{'with' if (side == 'up') == long else 'against'}"
    else:
        state = label
    adx, vol, er = regime["adx"], regime["volatility_pct"], regime["efficiency"]
    pos = regime["position_in_range"]
    late = pos >= 0.8 if long else pos <= 0.2
    early = pos <= 0.2 if long else pos >= 0.8
    return {
        "session": regime["nearest_open"],
        "adx": "<20" if adx < 20 else ("20-30" if adx < 30 else ">=30"),
        "volatility": "<0.3" if vol < 0.3 else ("0.3-0.7" if vol < 0.7 else ">=0.7"),
        "efficiency": "<0.3" if er < 0.3 else ("0.3-0.5" if er < 0.5 else ">=0.5"),
        "state": state,
        "range": "late" if late else ("early" if early else "middle"),
    }


class Filtered:
    """Wrap a strategy: a READY signal outside the kept bins becomes NO_TRADE."""

    def __init__(self, base, keep: dict[str, list[str]]) -> None:
        self.base, self.keep = base, keep
        self.name = base.name
        self.minimum_candles = base.minimum_candles

    def analyze(self, candles):
        setup = self.base.analyze(candles)
        if not setup.ready:
            return setup
        found = tags(regime_at(candles[-1].timestamp), setup.direction)
        if found and all(found[dim] in bins for dim, bins in self.keep.items()):
            return setup
        return MarketSetup("NO_TRADE", setup.timestamp, "Outside the market-state filter",
                           setup.bias, setup.strategy)


def backtest(period, keep: dict | None = None, spread_extra: float = 0.0) -> list[dict]:
    start, end = period
    before = [bar for bar in ENTRY if bar.timestamp < start][-(WINDOW + 10):]
    candles = before + [bar for bar in ENTRY if start <= bar.timestamp < end]
    trend = [bar for bar in TREND if bar.timestamp < end]
    config = BacktestConfig(resolution="MINUTE_5", trend_resolution="MINUTE_30", window=None,
                            management=None, risk=RiskConfig(max_trades_per_day=50, max_positions=3),
                            lookback=WINDOW, trend_lookback=WINDOW, label="5m-learn")
    strategies = [RetestStrategy(), PullbackStrategy(), DemandZoneStrategy()]
    if keep:
        strategies = [Filtered(s, keep) for s in strategies]
    result = run_backtest(candles, trend, config, strategies=strategies)
    spreads = {bar.timestamp: bar.spread for bar in ENTRY}
    rows = []
    for t in result.trades:
        if not start <= t.opened_at < end:
            continue
        risk = abs(t.entry - t.initial_stop)
        extra = spread_extra * spreads.get(t.opened_at, 0.0) / risk if risk else 0.0
        rows.append({"opened_at": t.opened_at.isoformat(), "direction": t.direction,
                     "strategy": t.strategy, "r": t.r - extra,
                     "tags": tags(regime_at(ENTRY[INDEX[t.opened_at] - 1].timestamp), t.direction)})
    return rows


def stats(rows: list[dict]) -> dict:
    rs = [row["r"] for row in rows]
    wins, losses = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    return {"trades": len(rs), "total_r": sum(rs), "average_r": sum(rs) / len(rs) if rs else 0.0,
            "profit_factor": wins / losses if losses else None}


def allowed(row: dict, keep: dict) -> bool:
    return row["tags"] is not None and all(row["tags"][dim] in bins for dim, bins in keep.items())


def discover(rows: list[dict]) -> dict:
    report: dict = {"baseline": stats(rows), "bins": {}, "candidates": []}
    singles = {}
    for dim in DIMENSIONS:
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            if row["tags"]:
                groups[row["tags"][dim]].append(row)
        report["bins"][dim] = {name: stats(group) for name, group in sorted(groups.items())}
        kept = sorted(name for name, group in groups.items()
                      if len(group) >= 300 and stats(group)["average_r"] > 0)
        if kept and len(kept) < len(groups):
            singles[dim] = kept
    for dim, kept in singles.items():
        report["candidates"].append({"keep": {dim: kept}, **stats([r for r in rows if allowed(r, {dim: kept})])})
    if len(singles) >= 2:
        top = sorted(report["candidates"], key=lambda c: -c["total_r"])[:2]
        keep = {**top[0]["keep"], **top[1]["keep"]}
        report["candidates"].append({"keep": keep, **stats([r for r in rows if allowed(r, keep)])})
    eligible = [c for c in report["candidates"] if c["trades"] >= 300 and c["average_r"] >= 0.05]
    report["locked"] = max(eligible, key=lambda c: c["total_r"]) if eligible else None
    return report


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources() -> dict:
    paths = [Path(__file__), PROTOCOL, ROOT / "src/goldbot/regime.py", ROOT / "src/goldbot/backtest.py"]
    return {str(p.relative_to(ROOT)): digest(p) for p in paths}


def quarters(rows: list[dict], start: datetime) -> list[float]:
    out = [0.0] * 4
    for row in rows:
        days = (datetime.fromisoformat(row["opened_at"]) - start).days
        out[min(3, int(days / (365.25 / 4)))] += row["r"]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("discover", "validate"))
    args = parser.parse_args()
    OUTPUT.mkdir(exist_ok=True)
    if args.stage == "discover":
        if (OUTPUT / "discovery.json").exists():
            raise SystemExit("Discovery already locked")
        rows = backtest(DISCOVERY)
        report = discover(rows)
        report["sources"] = sources()
        report["created_utc"] = datetime.now(U).isoformat()
        (OUTPUT / "discovery.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({k: report[k] for k in ("baseline", "candidates", "locked")}, indent=1))
        for dim, bins in report["bins"].items():
            print(dim, {k: (v["trades"], round(v["average_r"], 3)) for k, v in bins.items()})
    else:
        report = json.loads((OUTPUT / "discovery.json").read_text())
        if report["sources"] != sources():
            raise SystemExit("Sources changed after discovery")
        if (OUTPUT / "validation.json").exists():
            raise SystemExit("Validation already run")
        if not report["locked"]:
            raise SystemExit("Discovery locked nothing: the study failed at discovery")
        keep = report["locked"]["keep"]
        rows = backtest(VALIDATION, keep)
        stressed = backtest(VALIDATION, keep, spread_extra=0.5)
        base = backtest(VALIDATION)
        result = {"keep": keep, "filtered": stats(rows), "quarters": quarters(rows, VALIDATION[0]),
                  "stress_extra_spread": stats(stressed), "unfiltered": stats(base)}
        s = result["filtered"]
        result["passed"] = (s["trades"] >= 150 and s["average_r"] > 0.03
                            and (s["profit_factor"] or 0) > 1.05
                            and sum(q > 0 for q in result["quarters"]) >= 3
                            and result["stress_extra_spread"]["average_r"] > 0)
        (OUTPUT / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
