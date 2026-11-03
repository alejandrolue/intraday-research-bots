# Issue #18 results: US100/US30 at the New York cash open

Protocol: `INDEX_OPEN_PROTOCOL.md`. Training trials: 48 (`results_index_open/TRAINING.md`); eligible in year 1: 6. Validation: the 6 locked variants, run once.

| Locked variant | Status | Y1 trades / avg R | Y2 trades / avg R | Combined avg R | Positive quarters | Long/short R Y1 | Long/short R Y2 | Y2 avg R with +1 pt cost | Y2 bootstrap 95% | Pass |
|---|---|---|---|---|---|---|---|---|---|---|
| US100-orb-nycash-15-rr2-HOUR_4 | eligible | 161 / +0.174 | 146 / +0.095 | +0.136 | 7/8 | +22.0 / +6.0 | +2.1 / +11.8 | +0.079 | [-0.118, +0.339] | ✅ |
| US100-rolling_sweep-nycash-30-rr2-none | diagnostic | 350 / +0.002 | 348 / -0.023 | -0.010 | 4/8 | +2.0 / -1.2 | -8.1 / +0.2 | -0.038 | [-0.190, +0.140] | ❌ |
| US30-orb-nycash-15-rr2-none | eligible | 398 / +0.109 | 376 / -0.082 | +0.016 | 5/8 | +27.5 / +15.9 | -30.1 / -0.9 | -0.093 | [-0.210, +0.051] | ❌ |
| US30-rolling_sweep-nycash-30-rr2-none | diagnostic | 347 / -0.014 | 335 / -0.007 | -0.011 | 3/8 | -5.0 / +0.0 | -17.7 / +15.3 | -0.019 | [-0.177, +0.181] | ❌ |

### US100-orb-nycash-15-rr2-HOUR_4

Quarters (R): year 1 +3.0, +4.0, +16.0, +5.0 · year 2 +17.8, +3.0, +2.2, -9.2
PF 1.29 → 1.15; max DD 9.0R → 17.2R; median spread/stop 0.026; median hold 0.8 h

- ✅ training eligibility
- ✅ year 1 avg R > 0.05
- ✅ year 2 avg R > 0.05
- ✅ combined avg R > 0.05
- ✅ combined trades >= 300
- ✅ positive quarters >= 5/8
- ✅ long and short positive in year 1
- ✅ long and short positive in year 2

### US100-rolling_sweep-nycash-30-rr2-none

Quarters (R): year 1 -26.7, +30.0, -7.0, +4.5 · year 2 -3.0, +0.8, -12.0, +6.3
PF 1.00 → 0.97; max DD 27.7R → 38.5R; median spread/stop 0.025; median hold 1.0 h

- ❌ training eligibility
- ❌ year 1 avg R > 0.05
- ❌ year 2 avg R > 0.05
- ❌ combined avg R > 0.05
- ✅ combined trades >= 300
- ❌ positive quarters >= 5/8
- ❌ long and short positive in year 1
- ❌ long and short positive in year 2

### US30-orb-nycash-15-rr2-none

Quarters (R): year 1 +15.5, +5.0, +14.0, +8.9 · year 2 -1.1, +8.0, -3.8, -34.0
PF 1.17 → 0.88; max DD 19.0R → 41.0R; median spread/stop 0.019; median hold 0.7 h

- ✅ training eligibility
- ✅ year 1 avg R > 0.05
- ❌ year 2 avg R > 0.05
- ❌ combined avg R > 0.05
- ✅ combined trades >= 300
- ✅ positive quarters >= 5/8
- ✅ long and short positive in year 1
- ❌ long and short positive in year 2

### US30-rolling_sweep-nycash-30-rr2-none

Quarters (R): year 1 -9.0, -3.0, -7.0, +14.0 · year 2 -2.7, -2.0, +1.0, +1.3
PF 0.98 → 0.99; max DD 24.0R → 23.0R; median spread/stop 0.021; median hold 1.0 h

- ❌ training eligibility
- ❌ year 1 avg R > 0.05
- ❌ year 2 avg R > 0.05
- ❌ combined avg R > 0.05
- ✅ combined trades >= 300
- ❌ positive quarters >= 5/8
- ❌ long and short positive in year 1
- ❌ long and short positive in year 2

## Conclusion

Passed: US100-orb-nycash-15-rr2-HOUR_4. A pass is a candidate for a demo forward test, not proof of an edge; six locked variants across two related markets add selection risk.
