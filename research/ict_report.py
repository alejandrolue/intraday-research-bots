"""Issue #40: summarise the locked ICT training/validation outputs into RESULTS_ICT.md (reporting only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ict_study import TIMEFRAMES  # noqa: E402

TITLES = {"sb5m": "Silver Bullet, 5-minute", "sb1m": "Silver Bullet, 1-minute", "ts5m": "Turtle Soup, 5-minute"}


def main() -> None:
    lines = ["# Issue #40 results: ICT Silver Bullet and Turtle Soup", ""]
    for key, title in TITLES.items():
        spec = TIMEFRAMES[key]
        out = HERE / f"results_ict_{key}"
        training = json.loads((out / "training.json").read_text())
        selection = json.loads((out / "selection.json").read_text())
        test = {r["label"]: r for r in json.loads((out / "validation.json").read_text())["rows"]}
        train = {r["label"]: r for r in training["rows"]}
        rows = training["rows"]
        eligible = sum(1 for r in rows if r["trades"] >= spec["min_trades"] and r["average_r"] > 0.05
                       and all(s["total_r"] > 0 for s in r["side"].values())
                       and sum(q > 0 for q in r["buckets"]) >= spec["min_positive_buckets"])
        counts = [r["trades"] for r in rows]
        lines += [f"## {title}", "",
                  f"Training: {len(rows)} variants, {min(counts)}–{max(counts)} trades each, "
                  f"{sum(r['average_r'] > 0 for r in rows)} with positive average R, "
                  f"**{eligible} eligible** (minimum {spec['min_trades']} trades). "
                  f"All {len(selection['selected'])} locks are "
                  f"{'diagnostics' if not any(c['eligible'] for c in selection['selected']) else 'mixed'}, "
                  "so none can pass.", "",
                  "| Locked | Training trades / avg R | Holdout trades / avg R | Holdout long / short R |",
                  "|---|---|---|---|"]
        for choice in selection["selected"]:
            a, b = train[choice["id"]], test[choice["id"]]
            lines.append(f"| {choice['id']} | {a['trades']} / {a['average_r']:+.3f} | "
                         f"{b['trades']} / {b['average_r']:+.3f} | "
                         f"{b['side']['BUY']['total_r']:+.1f} / {b['side']['SELL']['total_r']:+.1f} |")
        best = sorted(rows, key=lambda r: -r["average_r"])[:3]
        lines += ["", "Best training variants (not validated): " + "; ".join(
            f"{r['label']} {r['trades']} trades {r['average_r']:+.3f}R" for r in best), ""]
    lines += ["## Conclusion", "",
              "No variant passed. Under the articles' rules (including the daily-bias filter) the Silver "
              "Bullet completes a setup only every few days, far too rarely to evaluate at one entry per "
              "window; its holdout results swing between -0.70R and +0.44R on 10–38 trades. Turtle Soup "
              "trades often enough but is near zero or negative out of sample. The only follow-up is a "
              "lab forward test of sb1m-GOLD-silverbullet-am-hour-fvg-liq (user-approved, post-hoc).", ""]
    (HERE / "RESULTS_ICT.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
