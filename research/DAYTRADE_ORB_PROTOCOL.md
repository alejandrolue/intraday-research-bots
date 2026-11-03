# Issue #85: day-trading opening-range breakout protocol

Written and committed before any result with the daily close was computed. Research only.

## The rule (fixed, no search)

The rule that passed issue #18 on US100, unchanged: `orb-<open>-15-rr2` (the 15-minute opening
range; enter on a close beyond it; stop and 2R target as in `session_strategies.py`), HOUR_4 trend
filter (EMA 20/50), one position, at most 3 entries per day, London–New York entry window, the
recorded spread charged, next-candle-open fills, stop first in ambiguous candles.

**New for day trading:** `flat_at`: every position still open at the candle ending at the daily
flat time is closed at that candle's close, and no entry is taken after it.

| Market | Open | Flat at |
|---|---|---|
| US100, US500, US30 | 09:30 New York | 15:45 New York |
| DE40 | 08:00 London (09:00 Frankfurt) | 17:15 Berlin |
| UK100 | 08:00 London | 16:15 London |

DE40 and UK100 have never been used by any study: for them this is a clean out-of-sample test of
the US100 rule. US100 itself was used to find the rule, and US500/US30 by the index studies (#18,
#31), so for those three the daily-close result is a re-check, not new evidence.

## Period

Trades opened in [2024-01-08, 2026-09-29), 5-minute candles.

## Pass rule (per market, recorded spreads)

At least 150 trades, average > +0.05R, profit factor > 1.1, positive in at least 2 of the 3
calendar years (2024, 2025, 2026), and still positive with an extra 50% of each trade's spread.
A day-trading bot is proposed only for markets that pass; DE40/UK100 passes count as new evidence.
The same runs without the daily close are reported as a diagnostic.
