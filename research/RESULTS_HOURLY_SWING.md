# Hourly range and squeeze-breakout study (issue #83)

**Failed.** 0 of 16 variants were eligible on 2019–2022 (all negative after costs). Validation on
2023 – Sep 2026 of the two locked diagnostics: the range rule lost; the breakout rule was slightly
positive but below the pre-registered bar and not eligible in training. Nothing is deployed.

## Training, 2019–2022 (5 markets, hourly)

- **Range (fade the edge in sideways markets):** −0.07 to −0.22R per trade over 565–1,947 trades.
  Shorts at the top of the range lost most (−91 to −179R): indices and gold mostly rose, so ranges
  broke upward more often than they held.
- **Squeeze breakout:** −0.04 to −0.11R per trade. The tighter box (≤ 3 ATR) with the stop beyond the
  opposite edge was least bad (−0.037R).
Full table: `results_hourly_swing/TRAINING.md`.

## Validation, 2023 – Sep 2026 (checked once)

| Locked diagnostic | Trades | Win | Avg R | Total R | PF | Max DD R | 2× spread | 6% fin. | Pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| range-loose-1.0-edge | 1,599 | 40% | −0.028 | −44.3 | 0.95 | 72.4 | −0.049 | −0.039 | No |
| breakout-3-opposite-3 | 576 | 43% | +0.030 | +17.5 | 1.06 | 34.4 | +0.021 | +0.010 | No (below +0.05R; not eligible in training) |

Breakout by year: 2023 +32.4R, 2024 +5.3R, 2025 −22.5R, 2026 +2.3R; by market GOLD +21.3R, DE40
+8.4R, US500 +2.0R, US30 −5.7R, US100 −8.5R.

**Diagnostic after the lock (no costs, not a selection input):** range −0.022R (train) / +0.003R
(validation); breakout −0.002R / +0.066R. Neither rule has a stable edge even before costs; the
breakout's positive validation comes from one strong year (2023) and gold.

## What it means

- Fading the range in "sideways" hourly markets does not work on these markets: sideways phases end
  in breakouts too often, mostly upward, and the fade pays for it.
- Squeeze breakouts are close to break-even; in 2023–26 slightly positive, but not robust across
  years or markets, and they failed the training rule.
- Together with #81 this closes the market-state idea for the current data: neither a filter on the
  5m signals nor dedicated range/breakout rules on hourly bars produced an edge that survives costs.
