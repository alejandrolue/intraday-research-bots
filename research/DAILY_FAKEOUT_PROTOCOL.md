# Issue #60: daily-frequency fakeout study protocol

Written and committed before any result of this study was computed. Research only: no live bot,
account, or order is changed.

## Question

Can the 5m V2 rules (#58, `evolution_strategy.py`, unchanged) trade about once a day, across four
markets, and still keep a positive edge after costs?

## Markets and data

GOLD, US100, US500 and US30, 5-minute candles from Capital.com with recorded bid/ask spreads.
Capital.com has no 5-minute data before January 2024.

- **Training (selection):** [2024-09-29, 2026-09-29). These two years were already used by earlier
  studies (#12, #27, #56, #58, the index studies), so they only serve to choose a variant.
- **Holdout (checked once):** [2024-01-08, 2024-08-01), about 7 months that no 5-minute study has
  used. The first trading week of 2024 is warm-up only.

Crossed or zero spreads are replaced by the previous candle's positive spread, as in earlier studies.

## Variants (64, one rule set for all four markets)

| Setting | Values |
| --- | --- |
| mode | `fakeout` only · `both` (fakeout + confluence, as in #58) |
| swing radius | 3 · 6 |
| zone window (bars) | 144 · 288 |
| target and minimum room (R) | 1.5 · 2.0 |
| trend filter | none · MINUTE_30 must agree |
| hours | all · London–New York (`TradingWindow`) |

All other `EvolutionConfig` values stay at their #58 defaults.

## Execution

`run_backtest` per market: next-candle-open fills, recorded spread, 0.5R entry-drift limit, stop
first when stop and target fall in the same candle, fixed exits, no management, one position per
market, at most 15 entries per market per UTC day. Spread filter: 0.80 points on GOLD (the live
default); none on the indices (the spread is still charged), as in the index studies (#18).

Extra-cost check: every trade pays another 50% of its recorded spread.

## Training eligibility (all required)

1. At least 500 trades over the two years (250 per year, about one per trading day).
2. Average above +0.05R per trade after spread.
3. Longs and shorts each positive.
4. At least 5 of 8 quarters positive.
5. At least 3 of 4 markets positive.

The single best eligible variant (highest average R, then lower drawdown, then name) is locked. If
none is eligible, the best variant with at least 500 trades is locked as a diagnostic, and the study
has failed.

## Holdout pass (all required, variant must also be training-eligible)

1. At least 140 trades (250 per year over 0.56 years).
2. Average above +0.05R per trade.
3. At least 4 of 7 calendar months positive.
4. At least 3 of 4 markets not negative.
5. Still positive with the extra 50% spread cost.

## Reporting

All 64 training rows; per-market, per-side, per-mode and per-quarter results; trades per year; the
locked choice with source hashes; the holdout result with a week-cluster bootstrap interval. The
harness refuses to re-select after locking and refuses a second holdout run. A failure is a valid
result, and nothing is deployed from this study without a separate user decision.
