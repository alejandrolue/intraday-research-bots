# Issue #83: hourly range and squeeze-breakout protocol

Written and committed before any result of these rules was computed. Research only; trading DE40
or any market outside DEMO_EPICS/LAB_EPICS live would need the user's approval.

## Why these rules

#81 showed that the 5-minute signals are trend signals: worst in sideways markets and never past
the spread. So two dedicated strategies are tested, each only in its own market state, on hourly
candles, where the spread is a far smaller share of the stop.

## Data

Capital.com HOUR candles, October 2018 – September 2026: GOLD, US100, US500, US30, DE40. Candles
without a recorded spread use 2× the market's median recorded spread as a fraction of price.

## Rules (`hourly_swing.py`, 16 variants)

Market state per candle comes from `goldbot.regime.market_regime` on the last 300 completed
candles; the range is the previous 20 candles (signal candle excluded).

**Range (sideways), 8 variants:** state efficiency < 0.3 and ADX < 20 (strict) or < 25 (loose).
Long when the candle's low reaches the bottom of the range (within 0.1 ATR), it closes back inside,
in the lower half, as an up candle; short mirrored at the top. Stop beyond the range extreme by 0.5
or 1.0 ATR. Target the range middle, or the opposite edge minus 0.25 ATR. Skipped if the target is
less than 0.8 × the risk. Time exit after 24 candles.

**Squeeze breakout, 8 variants:** previous 20-candle range at most 3 or 4 ATR wide. Long when the
candle closes above the range by ≥ 0.1 ATR, with a body ≥ 0.5 ATR and a close in the top quarter of
the candle; short mirrored. Stop at the range middle, or beyond the opposite edge by 0.1 ATR.
Target 2R or 3R. Time exit after 48 candles.

Execution: next-candle-open fills with half the spread, stop first in ambiguous candles, gaps fill
at the worse open, one position per market, financing 3% a year per calendar night (stress 6%).

## Periods

Training: fills in [2019-01-01, 2023-01-01). Validation, used once: [2023-01-01, 2026-09-29).

## Pass rules

Training eligibility (portfolio of 5 markets): ≥ 200 trades, average > +0.08R, profit factor > 1.15,
≥ 3 of 4 years positive, ≥ 3 of 5 markets positive, still positive with every spread doubled. One
variant per family is locked: the best eligible by total R (then lower drawdown, then name); if a
family has none, its best by average R with ≥ 200 trades is locked as a diagnostic.

Validation pass (eligible locked variants only): ≥ 150 trades, average > +0.05R, profit factor > 1.1,
≥ 3 of 4 years positive (2023–2026), ≥ 3 of 5 markets positive, still positive with doubled spreads
and with 6% financing. Two families are validated, so a pass leads to a demo forward test, not to
real money.
