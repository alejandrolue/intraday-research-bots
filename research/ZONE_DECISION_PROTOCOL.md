# Issue #56: zone-aware 5-minute Gold protocol

Written before evaluating candidate returns. Research only: nothing in this study changes a live
session, account, or order.

## Hypothesis

A higher-timeframe trend says direction but not location. A 5-minute continuation entry may still
run directly into fresh opposing supply or demand. A zone is therefore treated as a decision area:

1. `ema_room`: the existing EMA pullback signal is allowed only when the nearest intact opposing
   zone leaves at least 1.0R or 1.5R of room.
2. `rejection`: a candle probes an intact zone, then the next candle closes through the probe's
   opposite extreme in the rejection direction. The trade is taken only when the next opposing zone
   leaves room for the fixed target.
3. `breakout_retest`: one candle closes decisively through a zone and the next retests and holds the
   broken boundary. The next intact opposing zone must leave room for the fixed target.

The code is symmetric for demand/long and supply/short. It never reverses a blocked EMA signal.

## Zones and confirmation

- Swing highs/lows use radius 3 or 6 and become visible only after that many later candles.
- Only swings from the last 288 or 864 bars count. A close beyond a swing invalidates it.
- Same-kind swings within 0.25 ATR form one zone; a single fresh swing is also a zone. Zones extend
  0.25 ATR inward from their extreme.
- Probe/breakout buffer: 0.10 ATR. Retest tolerance: 0.20 ATR. Stop buffer: 0.25 ATR.
- ATR is the mean of 14 true ranges before the signal candle.
- Rejection and breakout targets are fixed at 1.5R or 2.0R. EMA pullbacks retain their existing
  1.5R target.

## Grid and execution

Ninety-six variants: family (3) x window (2) x radius (2) x parameter (2) x trend filter (none,
HOUR_4) x hours (all, London-New York). `parameter` is minimum room for `ema_room` and target R for
the other families.

Gold MINUTE_5, one position, at most 15 entries per UTC day, fixed exits, no management. The existing
`run_backtest(..., strategies=[candidate])` assumptions apply: recorded spread, next-bar-open fill,
0.5R maximum entry drift, and stop-first resolution when stop and target occur in the same bar.
Sensitivity subtracts another 0.20 price points from every trade.

## Train, lock, validate

- Training: [2024-09-29, 2025-09-29).
- Holdout: [2025-09-29, 2026-09-29).
- Training eligibility: at least 150 trades, average above +0.05R after recorded spread, both longs
  and shorts positive, and at least 3 of 4 positive quarters.
- One variant per family is locked from year 1: best eligible average R, then lower drawdown and ID.
  If none is eligible, lock the best diagnostic with at least 150 trades.
- Final pass: eligible in training, above +0.05R in each year and combined, at least 300 combined
  trades, at least 5 of 8 positive quarters, and longs and shorts positive in each year.

All 96 trials, selections, source hashes, repaired crossed-spread candles, holdout results, extra-cost
sensitivity, and a week-cluster bootstrap interval are reported. A failure is a valid result.
