# 5m-v1 volume pullback study (issue #63)

**Failed.** 0 of 48 variants were eligible on training, and the locked diagnostic lost clearly on
the unused holdout. Live, `5m-v1` runs only as a labelled demo experiment.

## Training, 2024-09-29 → 2026-09-29 (48 variants)

- **Volume rules mostly cut trades rather than adding edge.** With the 20-candle average, the
  pullback made +0.012R (2R) and +0.056R (3R) per trade over about 520 trades. Without any volume
  rule it made +0.021R and +0.057R over about 1,300 trades. Time-of-day relative volume looked best
  (+0.10 to +0.12R) but traded only 38 times a year, too rarely to be eligible.
- **The box entries barely exist.** With the volume conditions, no 8-candle box under 1.2 ATR
  with a 2× volume breakout ever formed (0 trades). Without volume, the breakout entry made 24
  trades a year and the retest entry lost.
- **Shorts lost in every frequent variant** (−24 to −48R); longs carried the results while gold
  rose.
- Locked diagnostic (best variant with ≥150 trades): `evp-pullback-off-rr3-all`, 1,245 trades
  (623 per year), +0.057R per trade, longs +113R, shorts −43R, 6/8 positive quarters, +0.028R with
  50% extra spread. Full table: `results_volume_pullback/TRAINING.md`.

## Holdout, 2024-01-08 → 2024-09-01 (checked once)

| Locked diagnostic | Trades | Per year | Avg R | Total R | Max DD R | +50% spread | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| evp-pullback-off-rr3-all | 419 | 646 | −0.217 | −91.0 | 98.2 | −0.282 | Fail |

Longs −36.0R, shorts −55.0R. Positive months: 2 of 8. By nearest open: London +8R (120 trades);
Tokyo −29R, NY cash −27R, COMEX −34R, US data −9R. Week-cluster bootstrap 95% interval: −0.37R to
−0.06R per trade, entirely below zero.

## Conclusion

The specification's pullback on 5-minute gold behaves like the earlier 5m strategies: slightly
positive on the years it was chosen on, clearly negative on new data. Tick volume did not add a
robust filter. If anything is followed up, it should be pre-registered on its own (for example
London-open longs only), not tuned from this holdout.
