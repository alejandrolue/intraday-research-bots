# Issue #12 results: 5-minute session strategies

Protocol: `FIVE_MINUTE_PROTOCOL.md`. Training trials: 48 (all listed in `results_5m/TRAINING.md`). Validation trials: 3 (the locked selections only, run once).

- Year 1 (selection): 2024-09-29 to 2025-09-29, 70,655 bars, median spread 0.30.
- Year 2 (holdout): 2025-09-29 to 2026-09-29, 70,879 bars, median spread 0.50.
- Eligible in year 1: 0 of 48 variants.

## orb-ny-60-rr1.6-none (diagnostic only)

| Year | Trades | Win | Avg R | Total R | PF | Max DD R | Long n / R | Short n / R | Avg R with +0.20 cost | Median spread/stop | Hold h median / max | Overnight | Year-end exits |
|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---|---:|---:|
| 1 (selection) | 318 | 40% | +0.031 | +9.9 | 1.05 | 31.8 | 160 / +22.0 | 158 / -12.1 | -0.005 | 0.051 | 0.5 / 65.3 | 30 | 0 |
| 2 (holdout) | 329 | 42% | +0.082 | +27.0 | 1.14 | 14.2 | 159 / +15.2 | 170 / +11.8 | +0.065 | 0.033 | 0.4 / 56.7 | 12 | 0 |

Quarters (R): year 1 +26.2, -9.2, -13.8, +6.7 · year 2 +11.0, +23.4, -4.0, -3.4

Holdout average R, week-block bootstrap 95% interval: [-0.049, +0.217]

**Research pass: NO**

- ❌ training eligibility
- ❌ year 1 avg R +0.031 > 0.05
- ✅ year 2 avg R +0.082 > 0.05
- ✅ combined avg R +0.057 > 0.05
- ✅ combined trades 647 >= 300
- ❌ positive quarters 4/8 >= 5
- ❌ long and short R positive in year 1
- ✅ long and short R positive in year 2

## rolling_sweep-ny-30-rr2-HOUR_4 (diagnostic only)

| Year | Trades | Win | Avg R | Total R | PF | Max DD R | Long n / R | Short n / R | Avg R with +0.20 cost | Median spread/stop | Hold h median / max | Overnight | Year-end exits |
|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---|---:|---:|
| 1 (selection) | 162 | 38% | +0.111 | +17.9 | 1.17 | 26.1 | 114 / +21.0 | 48 / -3.1 | +0.071 | 0.058 | 0.6 / 80.8 | 19 | 0 |
| 2 (holdout) | 172 | 35% | +0.064 | +11.0 | 1.10 | 10.0 | 103 / -13.0 | 69 / +24.0 | +0.046 | 0.033 | 0.7 / 59.0 | 8 | 0 |

Quarters (R): year 1 -6.1, +9.0, +7.1, +8.0 · year 2 -2.0, +0.0, +2.0, +11.0

Holdout average R, week-block bootstrap 95% interval: [-0.151, +0.272]

**Research pass: NO**

- ❌ training eligibility
- ✅ year 1 avg R +0.111 > 0.05
- ✅ year 2 avg R +0.064 > 0.05
- ✅ combined avg R +0.087 > 0.05
- ✅ combined trades 334 >= 300
- ✅ positive quarters 6/8 >= 5
- ❌ long and short R positive in year 1
- ❌ long and short R positive in year 2

## Conclusion

No selected strategy passed the fixed research criteria. Under this protocol, none of the three session families shows a reliable 5-minute edge after costs.

Limitations (from the protocol): spreads are sampled at each candle's close, not tick bid/ask; financing, commissions and slippage are not modeled beyond the +0.20-point sensitivity; selecting from 48 variants adds selection bias; this historical period had already been studied in the project, so the holdout is strategy-level, not pristine unseen data.
