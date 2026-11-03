# Daily-frequency fakeout study (issue #60)

**Failed.** The #58 rules cannot trade about daily across GOLD, US100, US500 and US30 without
losing. 0 of 64 variants were eligible on training; the locked diagnostic lost on the unused holdout.

## Training, 2024-09-29 → 2026-09-29 (64 variants)

- 37 variants reached ≥500 trades (≥250 per year). **None of them had a positive average.**
- Every variant with a positive average traded 58–141 times a year: the edge only appears when the
  rules are strict and rare, and it disappears as frequency rises.
- Best frequent variant, locked as the diagnostic: `both-r3-w144-rr2-MINUTE_30-all`, 755 trades
  (378/year), −0.010R per trade, shorts −52.5R, 3/8 positive quarters.
- The strongest rows (fakeout-only, MINUTE_30 trend, 144-bar window, 2R: +0.08 to +0.13R) trade
  64–139 times a year, and most of that came from GOLD and from longs. See `results_daily_fakeout/TRAINING.md`.

## Holdout, 2024-01-08 → 2024-08-01 (checked once)

| Locked diagnostic | Trades | Per year | Avg R | Total R | Max DD R | +50% spread | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| both-r3-w144-rr2-MINUTE_30-all | 212 | 376 | −0.109 | −23.1 | 37.1 | −0.176 | Fail |

By market: GOLD +11.0R (40 trades), US100 +6.0R (51), US500 −12.1R (69), US30 −28.0R (52).
Longs +0.9R, shorts −24.0R. Fakeout path −17.0R, confluence path −6.1R. Positive months: 2 of 7.
Week-cluster bootstrap 95% interval: −0.33R to +0.11R per trade.

## Conclusion

Frequency and edge trade off: on these rules, daily trading means giving up the edge. GOLD was the
only market that held up in both periods. Anything run live from this is a demo execution
experiment (like 5m-learn), not a validated strategy.
