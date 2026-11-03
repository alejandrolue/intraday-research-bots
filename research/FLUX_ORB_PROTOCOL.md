# Issue #31: Flux Charts ORB protocol (US500/US100, 5-minute and 1-minute)

Written before any candidate returns were evaluated. Research only. Same discipline as #12,
#18, #23 and #27, including amendment A1 (crossed-quote spreads replaced by the previous
candle's spread and listed).

## Strategy (from the article the user shared)

`src/goldbot/session_strategies.py`, session `nycash` (09:30 America/New_York):

- Opening range: the high and low of the first 5, 15, 30 or 60 minutes (every expected
  candle must exist, otherwise no trade that day).
- Entry: the first candle that **closes** above the range high (long) or below the range low
  (short), the previous candle having closed inside. No buffer, no retest, no volume filter.
  Entries from the end of the range until 175 minutes after the open.
- Stop: `orbside` = opposite side of the range ("conservative"); `orbmid` = range midpoint
  ("moderate").
- Target: fixed reward-to-risk of 1.0, 1.5 or 2.0.
- Daily bias: none, or the daily trend (`DAY` candles, 20/50 EMA filter as used elsewhere)
  must agree with the trade direction.
- **At most one entry per UTC day** (the article trades the breakout of the day's range),
  one position, fixed stop and target, no management.

## Markets and execution

US500 (≈ SPY) and US100 (≈ QQQ). `run_backtest` defaults: recorded spreads, next-open fills,
stop-first on ambiguous candles, 0.5R drift limit, London–NY entry window. The absolute spread
cap is disabled (index points; costs are still charged). The risk engine's minimum
reward-to-risk is set to 1.0 so the 1:1 variants can trade. Extra-cost sensitivity per trade:
US100 1.0 point, US500 0.25 point.

## Grids (96 variants per timeframe)

Market (US500, US100) × stop (orbside, orbmid) × range (5, 15, 30, 60 minutes) × reward
(1.0, 1.5, 2.0) × bias (none, DAY).

- **5-minute:** training [2024-09-29, 2025-09-29), holdout [2025-09-29, 2026-09-29).
  Rules exactly as #12 (≥150 trades, > +0.05R, longs and shorts positive, ≥3/4 positive
  quarters; pass: eligibility, > +0.05R in each year and combined, ≥300 trades, ≥5/8 positive
  quarters, longs and shorts positive in each year).
- **1-minute:** training [2025-10-01, 2026-04-01), holdout [2026-04-01, 2026-09-29).
  At one trade per day a half-year has at most ~125 trades, so the trade-count bars are scaled
  to the period length (fixed here, before any result): eligibility ≥75 trades, > +0.05R,
  longs and shorts positive, ≥4/6 positive months; pass: eligibility, > +0.05R in each half
  and combined, ≥150 trades in total, ≥8/12 positive months, longs and shorts positive in
  each half.

## Selection

Per timeframe, market and stop type (4 groups): the eligible variant with the highest average
R (then smallest drawdown, then ID); if none, the best meeting the timeframe's trade minimum
(150 for 5-minute, 75 for 1-minute) as a diagnostic, else the most-traded. Eight locked variants in total, committed before any holdout run, validated
once. All 192 training trials reported. This is the largest grid so far; any pass is a lab
candidate, not proof of an edge.
