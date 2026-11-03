# Issue #27: gold structure strategies protocol (5-minute and 1-minute)

Written before any candidate returns were evaluated. Research only. Same discipline as #12,
#18 and #23, including amendment A1 (crossed-quote spreads replaced by the previous candle's
spread and listed).

## Strategy (from the user's annotated charts)

`src/goldbot/structure_strategies.py`, two families:

- `zone`: price probes a swing level that already held once (a double bottom/top in the
  making) by at most 0.10 ATR past it and closes back on the right side with a rejection candle
  (green for longs, red for shorts). Stop 0.25 ATR beyond the probe or level.
- `channel`: price closes through the trendline drawn from the last two higher swing lows (for
  shorts) or lower swing highs (for longs), having been on the other side one candle earlier, by
  at least 0.10 ATR, with a candle in the break direction. Stop 0.25 ATR beyond the highest swing
  high (lowest swing low) since the second trendline point.
- **Target (both):** the nearest intact opposing swing level that gives at least `min_rr`;
  no such level, no trade.
- Swings: a candle whose high (low) is the highest (lowest) within `radius` candles on each side,
  confirmed only `radius` candles later. Levels within 0.25 ATR are one level. A level is broken
  by any later close beyond it (before the signal candle) and is then ignored. Only swings within
  the last `window` candles count. ATR = mean of the 14 true ranges before the signal candle.

## Execution (both timeframes)

GOLD, `run_backtest(..., strategies=[candidate])` with its defaults: recorded spreads, next-open
fills, stop-first on ambiguous candles, 0.5R drift limit, London–NY entry window with open
guards. One position, at most three entries per UTC day, fixed stop and target (no management).
Strategy look-back = `window` + 50 candles. R-based results, fixed 0.1 units. Extra-cost
sensitivity: 0.20 price points per trade.

## 5-minute grid (32 variants), two years

Family (zone, channel) × window (288 = 1 day, 864 = 3 days) × radius (3, 6) × min_rr (1.5, 2.5)
× trend filter (none, HOUR_4).
Training [2024-09-29, 2025-09-29); holdout [2025-09-29, 2026-09-29).
Eligibility and pass rules: exactly as #12 (≥150 trades, > +0.05R, longs and shorts positive,
≥3/4 positive quarters; pass: eligibility, > +0.05R in each year and combined, ≥300 trades,
≥5/8 positive quarters, longs and shorts positive in each year).

## 1-minute grid (32 variants), about one year

Family (zone, channel) × window (1440 = 1 day, 4320 = 3 days) × radius (5, 15) × min_rr
(1.5, 2.5) × trend filter (none, HOUR_4).
Only ~1 year of 1-minute history exists, so: training [2025-10-01, 2026-04-01); holdout
[2026-04-01, 2026-09-29). Each half starts flat with `window` + 50 warm-up candles.
Eligibility (training half): ≥150 trades, > +0.05R, longs and shorts positive, ≥4 of 6 positive
calendar months. Pass: eligibility, > +0.05R in each half and combined, ≥300 trades, ≥8 of 12
positive months, longs and shorts positive in each half.

## Selection

Per timeframe and family, the eligible variant with the highest average R (then smallest
drawdown, then ID); if none, the best with ≥150 trades as a diagnostic, else the most-traded.
Four locked variants in total (2 timeframes × 2 families), committed before any holdout run,
then validated once. All 64 training trials are reported.
