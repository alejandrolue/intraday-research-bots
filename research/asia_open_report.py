"""Issue #23: turn the locked Asia training/validation outputs into RESULTS_ASIA_OPEN.md.

Reporting only: reads research/results_asia_open/*.json and applies ASIA_OPEN_PROTOCOL.md.
"""

from __future__ import annotations

import json
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent / "results_asia_open"


def pf(value) -> str:
    return value if isinstance(value, str) else f"{value:.2f}"


def eligible(row: dict) -> bool:
    return (row["trades"] >= 150 and row["average_r"] > 0.05
            and all(s["total_r"] > 0 for s in row["side"].values())
            and sum(r > 0 for r in row["quarters"]) >= 3)


def main() -> None:
    training = json.loads((OUTPUT / "training.json").read_text())
    selection = json.loads((OUTPUT / "selection.json").read_text())
    validation = json.loads((OUTPUT / "validation.json").read_text())
    train = {r["label"]: r for r in training["rows"]}
    test = {r["label"]: r for r in validation["rows"]}
    lines = [
        "# Issue #23 results: Asia open (J225 + GOLD at Tokyo, HK50 at Hong Kong)", "",
        f"Protocol: `ASIA_OPEN_PROTOCOL.md`. Training trials: {selection['trial_count']} "
        f"(`results_asia_open/TRAINING.md`); eligible in year 1: "
        f"{sum(eligible(r) for r in training['rows'])}. Validation: the {len(test)} locked "
        "variants, run once.", "",
        "| Locked variant | Status | Y1 trades / avg R | Y2 trades / avg R | Combined avg R | "
        "Positive quarters | Long/short R Y1 | Long/short R Y2 | Y2 avg R with extra cost | "
        "Y2 bootstrap 95% | Pass |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    passed, details = [], []
    for choice in selection["selected"]:
        a, b = train[choice["id"]], test[choice["id"]]
        trades = a["trades"] + b["trades"]
        combined = (a["total_r"] + b["total_r"]) / trades if trades else 0.0
        quarters = a["quarters"] + b["quarters"]
        checks = [
            ("training eligibility", eligible(a)),
            ("year 1 avg R > 0.05", a["average_r"] > 0.05),
            ("year 2 avg R > 0.05", b["average_r"] > 0.05),
            ("combined avg R > 0.05", combined > 0.05),
            ("combined trades >= 300", trades >= 300),
            ("positive quarters >= 5/8", sum(q > 0 for q in quarters) >= 5),
            ("long and short positive in year 1", all(s["total_r"] > 0 for s in a["side"].values())),
            ("long and short positive in year 2", all(s["total_r"] > 0 for s in b["side"].values())),
        ]
        ok = all(value for _, value in checks)
        if ok:
            passed.append(choice["id"])
        low, high = b.get("week_bootstrap_95_interval", [0, 0])
        side = lambda r: f"{r['side']['BUY']['total_r']:+.1f} / {r['side']['SELL']['total_r']:+.1f}"
        lines.append(
            f"| {choice['id']} | {'eligible' if choice['eligible'] else 'diagnostic'} | "
            f"{a['trades']} / {a['average_r']:+.3f} | {b['trades']} / {b['average_r']:+.3f} | "
            f"{combined:+.3f} | {sum(q > 0 for q in quarters)}/8 | {side(a)} | {side(b)} | "
            f"{b['extra_cost_average_r']:+.3f} | [{low:+.3f}, {high:+.3f}] | {'✅' if ok else '❌'} |"
        )
        details += [
            f"### {choice['id']}", "",
            "Quarters (R): year 1 " + ", ".join(f"{q:+.1f}" for q in a["quarters"])
            + " · year 2 " + ", ".join(f"{q:+.1f}" for q in b["quarters"]),
            f"PF {pf(a["profit_factor"])} → {pf(b["profit_factor"])}; max DD {a['max_drawdown_r']:.1f}R → "
            f"{b['max_drawdown_r']:.1f}R; median spread/stop {b['median_spread_r']:.3f}; "
            f"median hold {b['median_hold_hours']:.1f} h", "",
            *[f"- {'✅' if value else '❌'} {label}" for label, value in checks], "",
        ]
    lines += ["", *details, "## Conclusion", "",
              (f"Passed: {', '.join(passed)}. A pass is a candidate for a demo forward test, not proof "
               "of an edge; six locked variants across two related markets add selection risk.")
              if passed else "No locked variant passed the fixed criteria.", ""]
    (OUTPUT.parent / "RESULTS_ASIA_OPEN.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
