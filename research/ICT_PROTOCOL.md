# Issue #40: ICT Silver Bullet and ICT Turtle Soup protocol

Written before any candidate returns were evaluated. Research only. Same discipline as #12,
#18, #23, #27 and #31 (year-1/first-half selection, locked one-time holdout, all trials
reported), including amendment A1 (crossed-quote spreads replaced by the previous candle's
spread and listed). Markets: GOLD, US100, US500. Where the articles are vague, the exact rule
used is stated here ("chosen:").

## ICT Silver Bullet (`src/goldbot/ict_strategies.py`, `silverbullet-...`)

- **Window** (America/New_York): `london` 03:00–04:00, `am` 10:00–11:00, `pm` 14:00–15:00.
  Every step below must happen inside the window; the entry candle must close inside it.
- **Liquidity** (chosen, two variants): `hour` = the high/low of the 60 minutes before the
  window opens; `day` = the high/low from 00:00 New York time until the window opens.
- **Sweep**: a window candle trades below the liquidity low (above the liquidity high) and a
  later or the same window candle closes back above (below) it. Sweep extreme = the lowest low
  (highest high) of the window up to that point.
- **Fair value gap** (chosen): three consecutive candles whose first high is below the third's
  low (bullish; mirror for bearish), with the third candle after the sweep candle. The most
  recent such FVG before the current candle is used.
- **Entry** (chosen): the **first** candle after the FVG whose low reaches the FVG top (high
  reaches the FVG bottom for shorts) and that closes at or beyond the FVG's far edge
  (close ≥ FVG bottom for longs). Market entry at that close (filled at the next open).
- **Stop** (two variants): `fvg` = 0.10 ATR beyond the FVG's far edge; `sweep` = 0.10 ATR
  beyond the sweep extreme.
- **Target** (two variants): `rr2` = 2R; `liq` = the opposite liquidity level (liquidity high
  for longs), only if at least 1R away, otherwise no trade.
- **Daily bias** (article: trade only in its direction): always on, as the DAY trend filter.
- One setup per window: if both a long and a short trigger on the same candle, no trade.

## ICT Turtle Soup (`turtlesoup-...`), 5-minute entries

- **Higher-timeframe liquidity** (chosen): the previous New York calendar day's high and low
  (`day`), or those plus 1-hour swing highs/lows (`dayh1`: an hourly candle built from the
  5-minute candles whose high/low is the extreme of the 2 hourly candles on each side, last 5
  days, confirmed only after those candles closed).
- **Sweep**: within the last 12 candles (1 hour), a candle trades below an SSL level (above a
  BSL level) and a candle closes back above (below) it. Sweep extreme = lowest low (highest
  high) since.
- **Market structure shift** (chosen): the most recent confirmed 5-minute swing high (low) with
  `radius` 2 or 4 that formed before the sweep candle; the entry candle is the first close
  above (below) it after the sweep.
- **Stop**: 0.10 ATR beyond the sweep extreme. **Target**: the nearest opposing HTF liquidity
  level giving at least `min_rr` (1.5 or 2.5); none, no trade.
- Daily bias: none or DAY (the article gives none; tested both).

## Execution (both)

`run_backtest` defaults: recorded spreads, next-open fills, stop-first on ambiguous candles,
0.5R drift limit. The London–NY entry window is **off** (the strategies carry their own
windows; the London Silver Bullet window starts at 03:00 New York). Absolute spread cap off
(costs still charged). One position; Silver Bullet at most one entry per UTC day, Turtle Soup
at most three. Fixed stop and target. Risk engine minimum reward-to-risk 1.0 (targets are
already constrained by the rules above). Extra-cost sensitivity per trade: GOLD 0.20,
US100 1.0, US500 0.25 points.

## Grids

- **Silver Bullet 5m** (2 years) and **Silver Bullet 1m** (~1 year): market (3) × window (3)
  × liquidity (2) × stop (2) × target (2) = **72 variants each**.
- **Turtle Soup 5m** (2 years): market (3) × levels (2) × radius (2) × min_rr (2) × bias (2)
  = **48 variants**.

Periods and rules: 5-minute as #12 (training [2024-09-29, 2025-09-29), holdout
[2025-09-29, 2026-09-29); eligibility ≥150 trades, > +0.05R, longs and shorts positive, ≥3/4
positive quarters; pass: eligibility, > +0.05R each year and combined, ≥300 trades, ≥5/8
positive quarters, longs and shorts positive each year). Because Silver Bullet trades at most
once a day, its 5-minute trade minimums are ≥100 (training) and ≥200 (combined).
1-minute as #31 (training [2025-10-01, 2026-04-01), holdout [2026-04-01, 2026-09-29);
eligibility ≥75 trades, > +0.05R, both sides positive, ≥4/6 positive months; pass:
eligibility, > +0.05R each half and combined, ≥150 trades, ≥8/12 positive months, both sides
positive each half).

## Selection

Per study, market and family-defining parameter (Silver Bullet: market × window, 9 groups;
Turtle Soup: market × levels, 6 groups): the eligible variant with the highest average R (then
smallest drawdown, then ID); if none, the best meeting the trade minimum as a diagnostic, else
the most-traded. Locked and committed before any holdout run, validated once. With 192
training trials and 24 locks this is heavy multiple testing; any pass is a lab candidate only.
