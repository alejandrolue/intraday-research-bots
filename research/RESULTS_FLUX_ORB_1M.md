# Issue #31 results: Flux Charts ORB (US500/US100), 1m

Training trials: 96 (`results_flux_orb_1m/TRAINING.md`); eligible: 8. Validation: 4 locked variants, run once.

| Locked | Status | Train trades / avg R | Holdout trades / avg R | Combined | Positive months | Long/short R train | Long/short R holdout | Holdout with extra cost | Holdout bootstrap 95% | Pass |
|---|---|---|---|---|---|---|---|---|---|---|
| 1m-US500-orbside-nycash-15-rr1-none | eligible | 125 / +0.139 | 124 / -0.016 | +0.061 | 7/12 | +7.3 / +10.0 | +6.0 / -8.0 | -0.031 | [-0.232, +0.203] | ❌ |
| 1m-US500-orbmid-nycash-30-rr1-none | eligible | 124 / +0.146 | 124 / +0.064 | +0.105 | 8/12 | +15.2 / +3.0 | +5.0 / +3.0 | +0.043 | [-0.108, +0.243] | ✅ |
| 1m-US100-orbside-nycash-15-rr1-none | eligible | 120 / +0.121 | 121 / -0.190 | -0.035 | 6/12 | +3.5 / +11.0 | -5.0 / -18.0 | -0.198 | [-0.357, -0.017] | ❌ |
| 1m-US100-orbmid-nycash-5-rr1.5-none | eligible | 127 / +0.142 | 128 / +0.091 | +0.116 | 9/12 | +7.5 / +10.5 | +10.2 / +1.5 | +0.072 | [-0.175, +0.351] | ✅ |

### 1m-US500-orbside-nycash-15-rr1-none

Months (R): training -0.1, +1.0, +11.0, +3.0, +8.0, -5.5 · holdout -5.0, -5.0, +2.0, -1.0, +1.0, +6.0

- ✅ training eligibility
- ✅ training avg R > 0.05
- ❌ holdout avg R > 0.05
- ✅ combined avg R > 0.05
- ✅ combined trades >= 150
- ❌ positive months >= 8/12
- ✅ long and short positive in training
- ❌ long and short positive in holdout

### 1m-US500-orbmid-nycash-30-rr1-none

Months (R): training +2.0, +3.0, +2.2, +7.0, +10.0, -6.0 · holdout +5.0, -5.0, +2.0, +6.0, -0.0, +0.0

- ✅ training eligibility
- ✅ training avg R > 0.05
- ✅ holdout avg R > 0.05
- ✅ combined avg R > 0.05
- ✅ combined trades >= 150
- ✅ positive months >= 8/12
- ✅ long and short positive in training
- ✅ long and short positive in holdout

### 1m-US100-orbside-nycash-15-rr1-none

Months (R): training +2.9, +3.0, +3.0, +2.0, +7.0, -3.4 · holdout -4.0, -5.0, -4.0, -3.0, -11.0, +4.0

- ✅ training eligibility
- ✅ training avg R > 0.05
- ❌ holdout avg R > 0.05
- ❌ combined avg R > 0.05
- ✅ combined trades >= 150
- ❌ positive months >= 8/12
- ✅ long and short positive in training
- ❌ long and short positive in holdout

### 1m-US100-orbmid-nycash-5-rr1.5-none

Months (R): training -3.0, +7.5, +1.5, +1.5, +12.5, -2.0 · holdout +4.0, +4.0, +0.5, +2.0, -6.3, +7.5

- ✅ training eligibility
- ✅ training avg R > 0.05
- ✅ holdout avg R > 0.05
- ✅ combined avg R > 0.05
- ✅ combined trades >= 150
- ✅ positive months >= 8/12
- ✅ long and short positive in training
- ✅ long and short positive in holdout
