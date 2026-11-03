# Issue #23 results: Asia open (J225 + GOLD at Tokyo, HK50 at Hong Kong)

Protocol: `ASIA_OPEN_PROTOCOL.md`. Training trials: 72 (`results_asia_open/TRAINING.md`); eligible in year 1: 0. Validation: the 9 locked variants, run once.

| Locked variant | Status | Y1 trades / avg R | Y2 trades / avg R | Combined avg R | Positive quarters | Long/short R Y1 | Long/short R Y2 | Y2 avg R with extra cost | Y2 bootstrap 95% | Pass |
|---|---|---|---|---|---|---|---|---|---|---|
| J225-orb-tokyo-15-rr1.6-none | diagnostic | 362 / -0.038 | 357 / +0.041 | +0.002 | 2/8 | -3.4 / -10.2 | +2.8 / +12.0 | +0.014 | [-0.112, +0.193] | ❌ |
| J225-rolling_sweep-tokyo-30-rr1.6-none | diagnostic | 294 / -0.009 | 326 / -0.107 | -0.060 | 4/8 | -13.6 / +11.0 | -18.6 / -16.2 | -0.136 | [-0.260, +0.056] | ❌ |
| GOLD-orb-tokyo-30-rr2-HOUR_4 | diagnostic | 175 / +0.029 | 171 / +0.000 | +0.014 | 4/8 | +15.0 / -10.0 | -8.0 / +8.0 | -0.031 | [-0.192, +0.210] | ❌ |
| GOLD-rolling_sweep-tokyo-15-rr2-none | diagnostic | 540 / -0.011 | 505 / -0.109 | -0.058 | 3/8 | +1.0 / -7.0 | -40.0 / -15.0 | -0.140 | [-0.220, +0.008] | ❌ |
| HK50-orb-hongkong-30-rr2-none | diagnostic | 255 / -0.047 | 239 / -0.034 | -0.041 | 4/8 | -20.0 / +7.9 | -6.3 / -1.9 | -0.064 | [-0.213, +0.144] | ❌ |
| HK50-rolling_sweep-hongkong-15-rr2-none | diagnostic | 385 / -0.135 | 398 / -0.100 | -0.118 | 1/8 | -12.1 / -40.0 | -17.0 / -23.0 | -0.137 | [-0.218, +0.029] | ❌ |

### J225-orb-tokyo-15-rr1.6-none

Quarters (R): year 1 -0.0, -13.8, -13.2, +13.4 · year 2 -4.6, -2.4, +23.2, -1.4
PF 0.94 → 1.07; max DD 44.6R → 19.8R; median spread/stop 0.050; median hold 0.8 h

- ❌ training eligibility
- ❌ year 1 avg R > 0.05
- ❌ year 2 avg R > 0.05
- ❌ combined avg R > 0.05
- ✅ combined trades >= 300
- ❌ positive quarters >= 5/8
- ❌ long and short positive in year 1
- ✅ long and short positive in year 2

### J225-rolling_sweep-tokyo-30-rr1.6-none

Quarters (R): year 1 -7.4, +10.4, +5.6, -11.2 · year 2 +0.0, -33.4, -9.0, +7.6
PF 0.99 → 0.84; max DD 28.6R → 52.8R; median spread/stop 0.052; median hold 1.1 h

- ❌ training eligibility
- ❌ year 1 avg R > 0.05
- ❌ year 2 avg R > 0.05
- ❌ combined avg R > 0.05
- ✅ combined trades >= 300
- ❌ positive quarters >= 5/8
- ❌ long and short positive in year 1
- ❌ long and short positive in year 2

### GOLD-orb-tokyo-30-rr2-HOUR_4

Quarters (R): year 1 -6.0, +3.0, +15.0, -7.0 · year 2 +8.0, -6.0, -2.0, +0.0
PF 1.04 → 1.00; max DD 12.0R → 25.0R; median spread/stop 0.046; median hold 0.5 h

- ❌ training eligibility
- ❌ year 1 avg R > 0.05
- ❌ year 2 avg R > 0.05
- ❌ combined avg R > 0.05
- ✅ combined trades >= 300
- ❌ positive quarters >= 5/8
- ❌ long and short positive in year 1
- ❌ long and short positive in year 2

### GOLD-rolling_sweep-tokyo-15-rr2-none

Quarters (R): year 1 +2.0, -30.0, +12.0, +10.0 · year 2 -6.0, -26.0, -17.0, -6.0
PF 0.98 → 0.85; max DD 41.0R → 81.0R; median spread/stop 0.047; median hold 0.5 h

- ❌ training eligibility
- ❌ year 1 avg R > 0.05
- ❌ year 2 avg R > 0.05
- ❌ combined avg R > 0.05
- ✅ combined trades >= 300
- ❌ positive quarters >= 5/8
- ❌ long and short positive in year 1
- ❌ long and short positive in year 2

### HK50-orb-hongkong-30-rr2-none

Quarters (R): year 1 +4.0, +1.0, -6.1, -11.0 · year 2 -8.0, +3.0, +13.1, -16.3
PF 0.93 → 0.95; max DD 27.1R → 20.0R; median spread/stop 0.060; median hold 2.7 h

- ❌ training eligibility
- ❌ year 1 avg R > 0.05
- ❌ year 2 avg R > 0.05
- ❌ combined avg R > 0.05
- ✅ combined trades >= 300
- ❌ positive quarters >= 5/8
- ❌ long and short positive in year 1
- ❌ long and short positive in year 2

### HK50-rolling_sweep-hongkong-15-rr2-none

Quarters (R): year 1 +9.0, -30.1, -17.0, -14.0 · year 2 -3.0, -1.1, -15.0, -20.9
PF 0.81 → 0.86; max DD 64.1R → 49.2R; median spread/stop 0.072; median hold 1.1 h

- ❌ training eligibility
- ❌ year 1 avg R > 0.05
- ❌ year 2 avg R > 0.05
- ❌ combined avg R > 0.05
- ✅ combined trades >= 300
- ❌ positive quarters >= 5/8
- ❌ long and short positive in year 1
- ❌ long and short positive in year 2

## Conclusion

No locked variant passed the fixed criteria.
