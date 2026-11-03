# Issue #69: inverted 5m-learn protocol

Written and committed before any backtest of the inverted variants was run. The only result seen
beforehand is the quick check on the 39 real 5m-learn closes posted in #69, which is a small,
streak-dominated sample and not part of this rule.

## Configuration (the live `5m-learn` setup)

GOLD MINUTE_5 entries with the three original strategies (breakout_retest, ema_pullback,
demand_zone_rejection), MINUTE_30 trend filter (EMA 20/50), all hours, fixed exits (no management),
at most 3 positions in one direction, 50 entries per day, 0.80 maximum spread, original
reward-to-risk ≥ 1.5, 499-candle windows as live. `run_backtest`: next-candle-open fills, recorded
spreads, 0.5R drift limit, stop first in ambiguous candles.

## Variants

| Variant | Direction | Stop / target | Trend filter applied to |
| --- | --- | --- | --- |
| original | as signalled | as signalled | the signal |
| mirror | opposite | same distance / same reward ratio on the other side | the original signal |
| exact | opposite | original target / original stop | the original signal |
| trend-keep | opposite | mirror geometry | the inverted trade |

Each runs with recorded spreads and again with zero spread (a diagnostic of how much is cost).

## Periods

- P0: [2024-01-08, 2024-09-29) — the earliest 5-minute data (the user accepts its prior use).
- Y1: [2024-09-29, 2025-09-29)
- Y2: [2025-09-29, 2026-09-29)

## Pass rule (recorded spreads)

An inverted variant passes only if, in **each** of P0, Y1 and Y2: average R > 0, profit factor
> 1.1, and at least 200 trades per year (140 for the shorter P0). Three inverted variants are
tested, so one may pass by luck: a pass leads to a separate demo forward test, never real money,
and `5m-learn` itself is not changed.
