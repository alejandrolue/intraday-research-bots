# Issue #12: fixed research protocol

Written before running candidate returns. All files here are research-only.

## Data and execution

- Cached Capital.com GOLD MINUTE_5 candles, read-only via a symlink. No downloads.
- Training: [2024-09-29 00:00 UTC, 2025-09-29 00:00 UTC).
- Holdout: [2025-09-29 00:00 UTC, 2026-09-29 00:00 UTC).
- Each year starts flat, with 300 entry warmup bars and earlier 4h history. Signals
  before the year boundary are suppressed. Remaining positions close at year end.
- Use `run_backtest(..., strategies=[candidate])`: recorded spreads, next-open fills,
  stop-first ambiguity, default 0.5R drift limit. One position at a time, at most
  three entries per UTC day, no pyramids/trailing. Keep the default London-NY entry
  window/open guards. Fixed units (0.1); report R, not a compounded investment return.
- Cached spread is sampled at each candle's close and reused for its OHLC execution.
  This is not historical tick bid/ask. Financing, commissions and slippage are absent
  from the engine. Report holding times/overnight trades and deduct an illustrative
  extra 0.20 price points per completed trade for slippage sensitivity. This does not
  simulate changed stop hits and does not establish profitability after every cost.

## Finite grid: 48 variants

Three families x two sessions x two range lengths x two rewards x two trend filters.

Families:

1. `orb`: close crosses an opening-range edge by 0.1 ATR; stop distance is the larger
   of half the opening range and 1.5 ATR.
2. A third family (not published).
3. `rolling_sweep`: same rejection rule using the preceding 6 or 12 candles' extremes.

All families are symmetric for long and short; skip candles sweeping both edges.
ATR is the mean of the preceding 14 true ranges (excluding the signal candle).
London session starts 08:00 Europe/London; NY starts 08:30 America/New_York, DST aware.
Opening ranges are 30 or 60 minutes. Entries run from the end of that range until
three hours after the session opens. Rolling ranges use 6 or 12 bars and the same
entry windows. Opening ranges must contain every expected five-minute candle;
rolling ranges must be contiguous. Reward/risk: 1.6 or 2.0. Trend filter: none or HOUR_4.
No directional, management, hour or parameter changes after viewing year 2.

## Selection and acceptance

First rank each family's variants using year 1 only. Eligibility requires >=150
trades, average R >0.05, positive long AND short total R, and >=3 of 4 positive
three-month research quarters (anchored to the year start, including zero-trade quarters).
Select the eligible candidate with highest average R (then smallest drawdown, then ID).
If a family has no eligible variant, lock its highest-average-R candidate with >=150
trades as a diagnostic only; if none has 150, lock the most-traded candidate.
Diagnostic candidates cannot be promoted based on a favorable holdout.

Write and commit the full training table and selection manifest before evaluating
year 2. Validate only these three locked candidates; do not inspect year 2 for the
other 45 variants or change selection. Report all 48 training trials and the three
validation trials, with no hidden retries or optimization.

A research pass requires training eligibility, >0.05R/trade in each year and combined,
>=300 combined trades, >=5 of 8 positive research quarters, and positive long AND short
R in each year. These conservative thresholds are fixed before testing. A pass is a
candidate for further independent testing, not proof of a persistent edge: searching
48 variants creates selection bias, and the prior project already inspected this
historical market period. This is a strategy-level holdout, not pristine unseen market data.

Report both years, eight quarters, side counts/R, profit factor, drawdown, median
spread/stop, holding times, year-end liquidations and a week-block bootstrap interval
for holdout expectancy (2,000 draws, fixed seed). No live changes in this PR.

## Amendment A1 (before any candidate returns were evaluated)

The first `train` run stopped at data validation: the cached MINUTE_5 history contains 44 of
147,209 candles (0.03%) with a negative close spread (bid above ask), the first at
2025-02-07 16:15 UTC. These crossed quotes are data errors with an unknown true spread.
Each is replaced by the previous candle's recorded spread, and every repaired timestamp is
listed in the audit (`repaired_nonpositive_spreads`). No candidate results existed when this
was decided, and nothing else in the protocol changed. (Claude Code, continuing Codex's work.)
