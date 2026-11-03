"""Issue #27: turn locked structure training/validation outputs into RESULTS_STRUCTURE_<tf>.md.

Reporting only; applies STRUCTURE_PROTOCOL.md.  Usage: python research/structure_report.py 5m
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PASS_BUCKETS = {"5m": 5, "1m": 8}
TRAIN_BUCKETS = {"5m": 3, "1m": 4}


def main(timeframe: str) -> None:
    out = HERE / f"results_structure_{timeframe}"
    training = json.loads((out / "training.json").read_text())
    selection = json.loads((out / "selection.json").read_text())
    validation = json.loads((out / "validation.json").read_text())
    train = {r["label"]: r for r in training["rows"]}
    test = {r["label"]: r for r in validation["rows"]}

    def eligible(r: dict) -> bool:
        return (r["trades"] >= 150 and r["average_r"] > 0.05
                and all(s["total_r"] > 0 for s in r["side"].values())
                and sum(q > 0 for q in r["buckets"]) >= TRAIN_BUCKETS[timeframe])

    unit = "quarters" if timeframe == "5m" else "months"
    lines = [f"# Issue #27 results: gold structure strategies, {timeframe}", "",
             f"Training trials: {selection['trial_count']} (`results_structure_{timeframe}/TRAINING.md`); "
             f"eligible: {sum(eligible(r) for r in training['rows'])}. Validation: "
             f"{len(test)} locked variants, run once.", "",
             f"| Locked | Status | Train trades / avg R | Holdout trades / avg R | Combined | Positive {unit} | "
             "Long/short R train | Long/short R holdout | Holdout with +0.20 cost | Holdout bootstrap 95% | Pass |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    details = []
    for choice in selection["selected"]:
        a, b = train[choice["id"]], test[choice["id"]]
        n = a["trades"] + b["trades"]
        combined = (a["total_r"] + b["total_r"]) / n if n else 0.0
        buckets = a["buckets"] + b["buckets"]
        checks = [
            ("training eligibility", eligible(a)),
            ("training avg R > 0.05", a["average_r"] > 0.05),
            ("holdout avg R > 0.05", b["average_r"] > 0.05),
            ("combined avg R > 0.05", combined > 0.05),
            ("combined trades >= 300", n >= 300),
            (f"positive {unit} >= {PASS_BUCKETS[timeframe]}/{len(buckets)}",
             sum(q > 0 for q in buckets) >= PASS_BUCKETS[timeframe]),
            ("long and short positive in training", all(s["total_r"] > 0 for s in a["side"].values())),
            ("long and short positive in holdout", all(s["total_r"] > 0 for s in b["side"].values())),
        ]
        ok = all(v for _, v in checks)
        side = lambda r: f"{r['side']['BUY']['total_r']:+.1f} / {r['side']['SELL']['total_r']:+.1f}"  # noqa: E731
        low, high = b.get("week_bootstrap_95_interval", [0, 0])
        lines.append(
            f"| {choice['id']} | {'eligible' if choice['eligible'] else 'diagnostic'} | "
            f"{a['trades']} / {a['average_r']:+.3f} | {b['trades']} / {b['average_r']:+.3f} | {combined:+.3f} | "
            f"{sum(q > 0 for q in buckets)}/{len(buckets)} | {side(a)} | {side(b)} | "
            f"{b['extra_cost_average_r']:+.3f} | [{low:+.3f}, {high:+.3f}] | {'✅' if ok else '❌'} |")
        details += [f"### {choice['id']}", "",
                    f"{unit.capitalize()} (R): training " + ", ".join(f"{q:+.1f}" for q in a["buckets"])
                    + " · holdout " + ", ".join(f"{q:+.1f}" for q in b["buckets"]), "",
                    *[f"- {'✅' if v else '❌'} {label}" for label, v in checks], ""]
    lines += ["", *details]
    (HERE / f"RESULTS_STRUCTURE_{timeframe.upper()}.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1])
