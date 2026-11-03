"""Issue #75: train, lock, validate daily trend-following across 11 markets."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research"))

from goldbot.history import _read  # noqa: E402
from trend_daily import RULES, Rule, simulate  # noqa: E402

HISTORY = ROOT / "data/history"
OUTPUT = ROOT / "research/results_trend_daily"
PROTOCOL = ROOT / "research/TREND_DAILY_PROTOCOL.md"
MARKETS = ("GOLD", "SILVER", "OIL_CRUDE", "US100", "US500", "US30", "DE40", "UK100", "J225",
           "HK50", "EURUSD")
TRAIN = (date(2008, 1, 1), date(2017, 1, 1))
VALID = (date(2017, 1, 1), date(2026, 9, 29))
BASE_RATE, STRESS_RATE = 0.03, 0.06


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict:
    paths = [Path(__file__), PROTOCOL, ROOT / "research/trend_daily.py"]
    return {str(path.relative_to(ROOT)): digest(path) for path in paths}


def load(epic: str) -> list:
    rows = {}
    for path in sorted((HISTORY / epic / "DAY").glob("*.csv")):
        for bar in _read(path):
            rows[bar.timestamp] = bar
    bars = [rows[key] for key in sorted(rows)]
    recorded = [bar.spread / bar.close for bar in bars if bar.spread > 0 and bar.close > 0]
    fraction = 2 * statistics.median(recorded)
    return [bar if bar.spread > 0 else replace(bar, spread=fraction * bar.close) for bar in bars]


DATA = {epic: load(epic) for epic in MARKETS}


def run(rule: Rule, period: tuple[date, date], *, rate: float = BASE_RATE,
        spread_mult: float = 1.0) -> dict:
    trades = [t for epic in MARKETS
              for t in simulate(epic, DATA[epic], rule, *period, financing_rate=rate,
                                spread_mult=spread_mult)]
    trades.sort(key=lambda t: t.closed)
    rs = [t.r for t in trades]
    wins, losses = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    years: dict[int, float] = defaultdict(float)
    markets: dict[str, float] = defaultdict(float)
    peak = equity = drawdown = 0.0
    for t in trades:
        years[t.closed.year] += t.r
        markets[t.market] += t.r
        equity += t.r
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    span = (period[1] - period[0]).days / 365.25
    return {
        "rule": rule.id, "family": rule.family, "trades": len(trades),
        "average_r": sum(rs) / len(rs) if rs else 0.0, "total_r": sum(rs),
        "r_per_year": sum(rs) / span, "profit_factor": wins / losses if losses else None,
        "max_drawdown_r": drawdown, "win_rate": sum(r > 0 for r in rs) / len(rs) if rs else 0.0,
        "long_r": sum(t.r for t in trades if t.direction == 1),
        "short_r": sum(t.r for t in trades if t.direction == -1),
        "years": {str(k): v for k, v in sorted(years.items())},
        "markets": {epic: markets.get(epic, 0.0) for epic in MARKETS},
        "median_days_held": statistics.median((t.closed - t.opened).days for t in trades) if trades else 0,
    }


def train_row(rule: Rule) -> dict:
    row = run(rule, TRAIN)
    row["stress_financing_average_r"] = run(rule, TRAIN, rate=STRESS_RATE)["average_r"]
    return row


def eligible(row: dict) -> bool:
    return (row["trades"] >= 150 and row["average_r"] > 0.10 and (row["profit_factor"] or 0) > 1.2
            and sum(v > 0 for v in row["years"].values()) >= 6
            and sum(v > 0 for v in row["markets"].values()) >= 6
            and row["stress_financing_average_r"] > 0)


def select(rows: list[dict]) -> list[dict]:
    chosen = []
    for family in ("donchian", "ma", "tsmom"):
        group = [row for row in rows if row["family"] == family]
        allowed = [row for row in group if eligible(row)]
        pool = allowed or [row for row in group if row["trades"] >= 150] or group
        best = sorted(pool, key=lambda r: (-r["r_per_year"], r["max_drawdown_r"], r["rule"]))[0]
        chosen.append({"rule": best["rule"], "eligible": bool(allowed)})
    return chosen


def validation_pass(row: dict, stress_fin: dict, stress_spread: dict, was_eligible: bool) -> bool:
    return (was_eligible and row["trades"] >= 150 and row["average_r"] > 0.05
            and (row["profit_factor"] or 0) > 1.1
            and sum(v > 0 for v in row["years"].values()) >= 6
            and sum(v > 0 for v in row["markets"].values()) >= 6
            and stress_fin["average_r"] > 0 and stress_spread["average_r"] > 0)


def table(rows: list[dict]) -> str:
    lines = ["# All 10 training variants (2008–2016, 11 markets, 3% financing)", "",
             "| Rule | Trades | Avg R | R/year | PF | Max DD R | +Years | +Markets | Long R | Short R | 6% fin. avg R | Eligible |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for row in sorted(rows, key=lambda r: -r["r_per_year"]):
        pf = row["profit_factor"]
        lines.append(
            f"| {row['rule']} | {row['trades']} | {row['average_r']:+.3f} | {row['r_per_year']:+.1f} | "
            f"{pf if pf is None else f'{pf:.2f}'} | {row['max_drawdown_r']:.1f} | "
            f"{sum(v > 0 for v in row['years'].values())}/{len(row['years'])} | "
            f"{sum(v > 0 for v in row['markets'].values())}/11 | {row['long_r']:+.1f} | "
            f"{row['short_r']:+.1f} | {row['stress_financing_average_r']:+.3f} | {eligible(row)} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("train", "validate"))
    args = parser.parse_args()
    OUTPUT.mkdir(exist_ok=True)
    rules = {rule.id: rule for rule in RULES}
    if args.stage == "train":
        if (OUTPUT / "selection.json").exists():
            raise SystemExit("Selection already locked")
        rows = [train_row(rule) for rule in RULES]
        (OUTPUT / "training.json").write_text(json.dumps(rows, indent=2) + "\n")
        (OUTPUT / "TRAINING.md").write_text(table(rows))
        (OUTPUT / "selection.json").write_text(json.dumps({
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "training_sha256": digest(OUTPUT / "training.json"),
            "source_hashes": source_hashes(), "selected": select(rows)}, indent=2) + "\n")
        print(table(rows))
        print("Locked:", select(rows))
    else:
        manifest = json.loads((OUTPUT / "selection.json").read_text())
        if manifest["source_hashes"] != source_hashes():
            raise SystemExit("Sources changed after selection")
        if (OUTPUT / "validation.json").exists():
            raise SystemExit("Validation already run")
        out = []
        for choice in manifest["selected"]:
            rule = rules[choice["rule"]]
            row = run(rule, VALID)
            fin = run(rule, VALID, rate=STRESS_RATE)
            spread = run(rule, VALID, spread_mult=2.0)
            row.update(stress_financing_average_r=fin["average_r"],
                       stress_spread_average_r=spread["average_r"],
                       eligible_in_training=choice["eligible"],
                       passed=validation_pass(row, fin, spread, choice["eligible"]))
            out.append(row)
        (OUTPUT / "validation.json").write_text(json.dumps(out, indent=2) + "\n")
        for row in out:
            print(json.dumps({k: row[k] for k in ("rule", "trades", "average_r", "r_per_year",
                  "profit_factor", "max_drawdown_r", "long_r", "short_r",
                  "stress_financing_average_r", "stress_spread_average_r", "passed")}))
            print("   years", {k: round(v, 1) for k, v in row["years"].items()})
            print("   markets", {k: round(v, 1) for k, v in row["markets"].items()})


if __name__ == "__main__":
    main()
