# Issue #27 results: gold structure strategies, 1m

Training trials: 32 (`results_structure_1m/TRAINING.md`); eligible: 0. Validation: 2 locked variants, run once.

| Locked | Status | Train trades / avg R | Holdout trades / avg R | Combined | Positive months | Long/short R train | Long/short R holdout | Holdout with +0.20 cost | Holdout bootstrap 95% | Pass |
|---|---|---|---|---|---|---|---|---|---|---|
| 1m-structure_zone-w1440-r5-rr2.5-none | diagnostic | 374 / +0.033 | 384 / -0.138 | -0.053 | 4/12 | +10.8 / +1.8 | -44.5 / -8.5 | -0.238 | [-0.326, +0.058] | ❌ |
| 1m-structure_channel-w1440-r5-rr2.5-none | diagnostic | 367 / +0.084 | 359 / -0.156 | -0.035 | 4/12 | -11.8 / +42.6 | -57.8 / +1.9 | -0.190 | [-0.311, +0.008] | ❌ |

### 1m-structure_zone-w1440-r5-rr2.5-none

Months (R): training +17.0, -8.6, +2.2, -5.1, -20.2, +27.3 · holdout -5.8, -13.4, -18.2, +3.0, -15.3, -3.4

- ❌ training eligibility
- ❌ training avg R > 0.05
- ❌ holdout avg R > 0.05
- ❌ combined avg R > 0.05
- ✅ combined trades >= 300
- ❌ positive months >= 8/12
- ✅ long and short positive in training
- ❌ long and short positive in holdout

### 1m-structure_channel-w1440-r5-rr2.5-none

Months (R): training +31.8, +21.0, -14.1, -7.7, -12.0, +11.8 · holdout -1.0, -11.4, -22.2, +4.2, -20.3, -5.2

- ❌ training eligibility
- ✅ training avg R > 0.05
- ❌ holdout avg R > 0.05
- ❌ combined avg R > 0.05
- ✅ combined trades >= 300
- ❌ positive months >= 8/12
- ❌ long and short positive in training
- ❌ long and short positive in holdout
