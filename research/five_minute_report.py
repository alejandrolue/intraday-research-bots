"""Issue #12: turn the locked training/validation outputs into RESULTS.md.

Reporting only: reads research/results_5m/*.json and applies the acceptance rules from
FIVE_MINUTE_PROTOCOL.md. It never reruns or reselects anything.

    python research/five_minute_report.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent / "results_5m"


def load(name: str) -> dict:
    return json.loads((OUTPUT / name).read_text())


def eligible(row: dict) -> bool:
    return (row["trades"] >= 150 and row["average_r"] > 0.05
            and all(s["total_r"] > 0 for s in row["side"].values())
            and sum(r > 0 for r in row["quarters"]) >= 3)


def verdict(train: dict, test: dict) -> tuple[bool, list[str]]:
    trades = train["trades"] + test["trades"]
    combined = (train["total_r"] + test["total_r"]) / trades if trades else 0.0
    quarters = train["quarters"] + test["quarters"]
    checks = [
        ("training eligibility", eligible(train)),
        (f"year 1 avg R {train['average_r']:+.3f} > 0.05", train["average_r"] > 0.05),
        (f"year 2 avg R {test['average_r']:+.3f} > 0.05", test["average_r"] > 0.05),
        (f"combined avg R {combined:+.3f} > 0.05", combined > 0.05),
        (f"combined trades {trades} >= 300", trades >= 300),
        (f"positive quarters {sum(q > 0 for q in quarters)}/8 >= 5",
         sum(q > 0 for q in quarters) >= 5),
        ("long and short R positive in year 1",
         all(s["total_r"] > 0 for s in train["side"].values())),
        ("long and short R positive in year 2",
         all(s["total_r"] > 0 for s in test["side"].values())),
    ]
    lines = [f"- {'✅' if ok else '❌'} {label}" for label, ok in checks]
    return all(ok for _, ok in checks), lines


def pf(value: float) -> str:
    return "inf" if value == math.inf else f"{value:.2f}"


def year_rows(label: str, row: dict) -> str:
    buy, sell = row["side"]["BUY"], row["side"]["SELL"]
    return (
        f"| {label} | {row['trades']} | {row['win_rate']:.0%} | {row['average_r']:+.3f} | "
        f"{row['total_r']:+.1f} | {pf(row['profit_factor'])} | {row['max_drawdown_r']:.1f} | "
        f"{buy['trades']} / {buy['total_r']:+.1f} | {sell['trades']} / {sell['total_r']:+.1f} | "
        f"{row['extra_020_cost_average_r']:+.3f} | {row['median_spread_r']:.3f} | "
        f"{row['median_hold_hours']:.1f} / {row['max_hold_hours']:.1f} | {row['overnight_utc']} | "
        f"{row['exit_reasons'].get('END_OF_DATA', 0)} |"
    )


def main() -> None:
    training = load("training.json")
    selection = load("selection.json")
    validation = load("validation.json")
    train_rows = {row["label"]: row for row in training["rows"]}
    test_rows = {row["label"]: row for row in validation["rows"]}

    lines = [
        "# Issue #12 results: 5-minute session strategies",
        "",
        f"Protocol: `FIVE_MINUTE_PROTOCOL.md`. Training trials: {selection['trial_count']} "
        f"(all listed in `results_5m/TRAINING.md`). Validation trials: {len(test_rows)} "
        "(the locked selections only, run once).",
        "",
        f"- Year 1 (selection): {training['data']['start_inclusive'][:10]} to "
        f"{training['data']['end_exclusive'][:10]}, {training['data']['bars']:,} bars, "
        f"median spread {training['data']['median_spread']:.2f}.",
        f"- Year 2 (holdout): {validation['data']['start_inclusive'][:10]} to "
        f"{validation['data']['end_exclusive'][:10]}, {validation['data']['bars']:,} bars, "
        f"median spread {validation['data']['median_spread']:.2f}.",
        f"- Eligible in year 1: {sum(eligible(r) for r in training['rows'])} of "
        f"{len(training['rows'])} variants.",
        "",
    ]
    passed = []
    for choice in selection["selected"]:
        label = choice["id"]
        train, test = train_rows[label], test_rows[label]
        ok, checks = verdict(train, test)
        if ok:
            passed.append(label)
        low, high = test.get("week_bootstrap_95_interval", [0, 0])
        lines += [
            f"## {label} ({'eligible' if choice['eligible'] else 'diagnostic only'})",
            "",
            "| Year | Trades | Win | Avg R | Total R | PF | Max DD R | Long n / R | Short n / R | "
            "Avg R with +0.20 cost | Median spread/stop | Hold h median / max | Overnight | "
            "Year-end exits |",
            "|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---|---:|---:|",
            year_rows("1 (selection)", train),
            year_rows("2 (holdout)", test),
            "",
            "Quarters (R): year 1 " + ", ".join(f"{q:+.1f}" for q in train["quarters"])
            + " · year 2 " + ", ".join(f"{q:+.1f}" for q in test["quarters"]),
            "",
            f"Holdout average R, week-block bootstrap 95% interval: [{low:+.3f}, {high:+.3f}]",
            "",
            f"**Research pass: {'YES' if ok else 'NO'}**",
            "",
            *checks,
            "",
        ]
    lines += [
        "## Conclusion",
        "",
        (
            f"Passed the fixed research criteria: {', '.join(passed)}. A pass is a candidate "
            "for further independent (forward/demo) testing, not proof of a persistent edge."
            if passed
            else "No selected strategy passed the fixed research criteria. Under this protocol, "
            "none of the three session families shows a reliable 5-minute edge after costs."
        ),
        "",
        "Limitations (from the protocol): spreads are sampled at each candle's close, not tick "
        "bid/ask; financing, commissions and slippage are not modeled beyond the +0.20-point "
        "sensitivity; selecting from 48 variants adds selection bias; this historical period "
        "had already been studied in the project, so the holdout is strategy-level, not "
        "pristine unseen data.",
        "",
    ]
    (OUTPUT.parent / "RESULTS_5M.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
