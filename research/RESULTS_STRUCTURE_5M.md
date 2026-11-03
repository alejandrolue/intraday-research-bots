# Issue #27 results: gold structure strategies, 5m

Training trials: 32 (`results_structure_5m/TRAINING.md`); eligible: 0. Validation: 2 locked variants, run once.

| Locked | Status | Train trades / avg R | Holdout trades / avg R | Combined | Positive quarters | Long/short R train | Long/short R holdout | Holdout with +0.20 cost | Holdout bootstrap 95% | Pass |
|---|---|---|---|---|---|---|---|---|---|---|
| 5m-structure_zone-w288-r6-rr2.5-HOUR_4 | diagnostic | 295 / +0.113 | 280 / +0.242 | +0.176 | 4/8 | +24.0 / +9.3 | +64.4 / +3.2 | +0.202 | [-0.099, +0.619] | ❌ |
| 5m-structure_channel-w864-r6-rr1.5-HOUR_4 | diagnostic | 204 / +0.106 | 264 / -0.013 | +0.039 | 3/8 | +25.9 / -4.4 | -18.5 / +15.1 | -0.029 | [-0.198, +0.184] | ❌ |

### 5m-structure_zone-w288-r6-rr2.5-HOUR_4

Quarters (R): training -1.8, +14.4, -18.9, +39.6 · holdout +72.0, -2.9, +1.3, -2.9

- ❌ training eligibility
- ✅ training avg R > 0.05
- ✅ holdout avg R > 0.05
- ✅ combined avg R > 0.05
- ✅ combined trades >= 300
- ❌ positive quarters >= 5/8
- ✅ long and short positive in training
- ✅ long and short positive in holdout

### 5m-structure_channel-w864-r6-rr1.5-HOUR_4

Quarters (R): training -7.5, +2.7, -17.3, +43.6 · holdout -3.3, -9.3, -12.7, +21.9

- ❌ training eligibility
- ✅ training avg R > 0.05
- ❌ holdout avg R > 0.05
- ❌ combined avg R > 0.05
- ✅ combined trades >= 300
- ❌ positive quarters >= 5/8
- ❌ long and short positive in training
- ❌ long and short positive in holdout
