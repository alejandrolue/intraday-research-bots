# Issue #31 results: Flux Charts ORB (US500/US100), 5m

Training trials: 96 (`results_flux_orb_5m/TRAINING.md`); eligible: 16. Validation: 4 locked variants, run once.

| Locked | Status | Train trades / avg R | Holdout trades / avg R | Combined | Positive quarters | Long/short R train | Long/short R holdout | Holdout with extra cost | Holdout bootstrap 95% | Pass |
|---|---|---|---|---|---|---|---|---|---|---|
| 5m-US500-orbside-nycash-30-rr2-none | eligible | 161 / +0.219 | 179 / -0.009 | +0.099 | 6/8 | +18.1 / +17.2 | -8.0 / +6.5 | -0.019 | [-0.231, +0.228] | ❌ |
| 5m-US500-orbmid-nycash-15-rr2-none | eligible | 250 / +0.130 | 254 / +0.010 | +0.069 | 7/8 | +16.8 / +15.7 | -3.5 / +6.0 | -0.012 | [-0.170, +0.199] | ❌ |
| 5m-US100-orbside-nycash-15-rr2-none | eligible | 190 / +0.190 | 196 / +0.082 | +0.135 | 7/8 | +29.3 / +6.8 | -2.1 / +18.1 | +0.074 | [-0.126, +0.312] | ❌ |
| 5m-US100-orbmid-nycash-15-rr2-none | diagnostic | 241 / +0.120 | 245 / -0.028 | +0.046 | 5/8 | +45.0 / -16.0 | -6.5 / -0.4 | -0.042 | [-0.225, +0.191] | ❌ |

### 5m-US500-orbside-nycash-30-rr2-none

Quarters (R): training +11.0, +8.0, +1.2, +15.1 · holdout +3.0, -10.4, -0.2, +6.0

- ✅ training eligibility
- ✅ training avg R > 0.05
- ❌ holdout avg R > 0.05
- ✅ combined avg R > 0.05
- ✅ combined trades >= 300
- ✅ positive quarters >= 5/8
- ✅ long and short positive in training
- ❌ long and short positive in holdout

### 5m-US500-orbmid-nycash-15-rr2-none

Quarters (R): training +15.0, +9.7, +3.0, +4.8 · holdout +6.0, +4.5, -15.0, +7.0

- ✅ training eligibility
- ✅ training avg R > 0.05
- ❌ holdout avg R > 0.05
- ✅ combined avg R > 0.05
- ✅ combined trades >= 300
- ✅ positive quarters >= 5/8
- ✅ long and short positive in training
- ❌ long and short positive in holdout

### 5m-US100-orbside-nycash-15-rr2-none

Quarters (R): training +5.8, +14.0, +2.3, +14.0 · holdout +17.0, -2.5, +0.4, +1.1

- ✅ training eligibility
- ✅ training avg R > 0.05
- ✅ holdout avg R > 0.05
- ✅ combined avg R > 0.05
- ✅ combined trades >= 300
- ✅ positive quarters >= 5/8
- ✅ long and short positive in training
- ❌ long and short positive in holdout

### 5m-US100-orbmid-nycash-15-rr2-none

Quarters (R): training -2.0, +5.0, +19.0, +7.0 · holdout +2.0, +5.5, -11.0, -3.4

- ❌ training eligibility
- ✅ training avg R > 0.05
- ❌ holdout avg R > 0.05
- ❌ combined avg R > 0.05
- ✅ combined trades >= 300
- ✅ positive quarters >= 5/8
- ❌ long and short positive in training
- ❌ long and short positive in holdout
