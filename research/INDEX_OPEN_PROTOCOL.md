# Issue #18: US index cash-open protocol (portfolio part 1)

Written before any candidate returns were evaluated. Research only. It follows the issue #12
protocol (`FIVE_MINUTE_PROTOCOL.md`), including amendment A1 (crossed-quote spreads are
replaced by the previous candle's spread and listed), with the differences stated here.

## Data and execution

- Cached Capital.com MINUTE_5 candles for **US100** and **US30**, plus HOUR_4 for the trend
  filter, read-only. Same split as #12: training [2024-09-29, 2025-09-29), holdout
  [2025-09-29, 2026-09-29). Each year starts flat with 300 warm-up bars; signals before the
  boundary are suppressed; open positions close at year end.
- `run_backtest(..., strategies=[candidate])` with its defaults: recorded spreads, next-open
  fills, stop-first on ambiguous candles, 0.5R drift limit, London–NY entry window.
- One position at a time, at most three entries per UTC day, fixed stop and target (no
  breakeven, trailing, target extension or adds). R-based results; fixed 0.1 units.
- **Difference from #12:** the risk engine's absolute spread cap (0.80 price points, sized for
  gold) is disabled, because index spreads are quoted in index points (~2). Spread costs are
  still charged on every fill. Report the median spread as a share of the stop distance.
- Cost sensitivity: deduct an extra **1.0 index point per completed trade** (about half a
  typical index spread, the counterpart of #12's 0.20 points on gold) and report it.

## Finite grid: 48 variants

Two markets × three families × two range lengths × two rewards × two trend filters.

- Families: `orb`, `rolling_sweep` and a third family that is not published, logic unchanged from #12
  (`src/goldbot/session_strategies.py`, parity-tested against the #12 research code).
- Session: `nycash`, the 09:30 America/New_York stock-market cash open (DST aware).
- Range: **15 or 30 minutes** (rolling sweep: 3 or 6 candles). Entries from the end of the
  range until 175 minutes after the open, as in #12.
- Reward/risk: 1.6 or 2.0. Trend filter: none or HOUR_4 (of the same market).

## Selection and acceptance

Identical rules to #12, applied **per market**: rank each (market, family) group on year 1 only.
Eligible: ≥150 trades, average R > +0.05, positive long AND short total R, ≥3 of 4 positive
quarters. Lock the eligible variant with the highest average R (then smallest drawdown, then
ID); if none is eligible, lock the highest-average-R variant with ≥150 trades as a diagnostic
(otherwise the most-traded). That locks **six** variants (2 markets × 3 families), committed
before year 2 is evaluated. Validate only those six, once.

A research pass requires training eligibility, > +0.05R/trade in each year and combined,
≥300 combined trades, ≥5 of 8 positive quarters, and positive long AND short R in each year.
Report all 48 training trials and the six validation trials.

Six locked candidates across two related markets means more chances of a lucky pass than in
#12; any pass is a candidate for a demo forward test, not proof of an edge.
