# Issue #63: 5m-v1 volume pullback protocol

Written and committed before any return of this strategy was computed. A smoke test on two weeks of
October 2025 only counted signals and skip reasons (no P&L) to check speed and wiring. Research
only: no live bot, account, or order is changed by this study.

## Strategy (fixed, `src/goldbot/volume_pullback.py`)

The user's specification in #63 with the issue's proposed numbers:

- 30m bias (`Ema21TrendFilter`): the last two completed 30m closes beyond EMA 21, the latest by at
  least 0.25 × ATR30(14); neutral when the latest 30m close is within 0.5 × ATR30 of EMA 200.
  (The spec's "entry price near EMA 200" is approximated by the latest completed 30m close.)
- Pullback: EMA 9 > EMA 21 throughout; swing high = highest of the previous 12 candles; 1–6
  pullback candles; at least one low in [EMA21 − 0.1 ATR, EMA9]; no close below EMA 21; impulse
  candle with volume above baseline in the 12 candles up to the swing; every pullback candle below
  baseline; engulfing or pin-bar trigger closing above EMA 9. Stop entry − 2 ATR; skipped if the
  pullback low − 0.1 ATR is below it.
- Box: 8 candles with range ≤ 1.2 × ATR (from before the box) and mean volume below baseline;
  breakout close beyond the edge by ≥ 0.1 ATR with a volume spike (≥ 2× baseline). Retest (A): up
  to 6 candles, no close back inside, a touch of the edge or EMA 9, dry-up, trigger candle; stop
  under the retest low. Breakout (B): enter at the breakout close, not within 15 minutes after a
  market open; stop under the box. Both skipped if the stop exceeds 2 ATR.
- Guards: no entries in the first 15 minutes after a break, when the spread is above 2× the
  median of the same time slot on the previous 3 days, or inside a news blackout (the calendar
  only covers Oct–Nov 2026, so it does not affect the backtest).
- Volume baseline: `sma` = 20-candle average (impulse > 1.0, dry-up < 1.0); `rvol` = same
  5-minute slot on the previous 3 trading days (impulse > 1.5, dry-up < 1.0); `off` = volume rules
  ignored (a diagnostic of whether volume adds anything).

## Variants (48)

entry (pullback · retest · breakout · combined = pullback or retest) × volume (sma · rvol · off) ×
target (2R · 3R) × hours (all · London–New York).

## Execution

GOLD MINUTE_5, MINUTE_30 bias, `run_backtest`: next-candle-open fills, recorded spread, 0.80
maximum spread, 0.5R drift limit, stop first in ambiguous candles, fixed exits, one position, at
most 6 entries per day. Live parity: 999 completed 5m candles and 999 completed 30m candles per
decision (the live session fetches 1000 with `--price-count 1000`).

## Periods

- Training: [2024-09-29, 2026-09-29). Already used by earlier 5m studies; selection only.
- Holdout (once): [2024-01-08, 2024-09-01), 8 months no 5m gold study has used.

## Rules

Training eligibility: ≥ 150 trades, average > +0.05R after spread, longs and shorts each positive,
≥ 5 of 8 quarters positive. Lock the best eligible variant (average R, then drawdown, then name);
if none, lock the best with ≥ 150 trades (or the most trades) as a diagnostic, and the study fails.

Holdout pass: training-eligible, ≥ 50 trades, average > +0.05R, ≥ 4 of 8 months positive, still
positive with an extra 50% of each trade's spread.

Reported: all 48 rows, side, quarter and nearest-open splits, the locked choice with source
hashes, and the holdout with a week-cluster bootstrap interval. The live session `5m-v1` (demo
account "5m-v1") runs the locked variant afterwards at the user's request, labelled with the
result; a failure means it runs as a demo experiment only.
