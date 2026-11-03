# Issue #81: market-state filter for the 5m-learn signals

Written and committed before any discovery or validation number was computed. The only related
numbers seen beforehand are the 27 live trades of 7 Oct 2026 (one trend day, sliced seven ways),
which are not part of this rule and are not used to choose anything.

## Signals and execution (the live 5m-learn configuration)

GOLD MINUTE_5, the three original strategies, MINUTE_30 trend filter (EMA 20/50), all hours,
fixed exits, at most 3 positions in one direction, 50 entries per day, 0.80 maximum spread,
reward-to-risk ≥ 1.5, 499-candle windows. `run_backtest`: next-open fills, recorded spreads, 0.5R
drift limit, stop first in ambiguous candles. R is net of the recorded spread.

## Market state of a trade

`goldbot.regime.market_regime` on the 499-candle window ending with the signal candle (exactly as
logged live), read relative to the trade's direction. Six dimensions with fixed bins:

| Dimension | Bins |
|---|---|
| session | nearest open: tokyo, london, comex, us-data, ny-cash |
| adx | < 20, 20–30, ≥ 30 |
| volatility | ATR percentile < 0.3, 0.3–0.7, ≥ 0.7 |
| efficiency | < 0.3, 0.3–0.5, ≥ 0.5 |
| state | trend_with, trend_against, breakout_with, breakout_against, sideways, mixed (trend/breakout labels relative to the trade direction) |
| range | late (sell in the bottom 20% / buy in the top 20% of the 20-candle range), early (the opposite 20%), middle |

## Discovery: [2024-01-08, 2025-09-29)

1. One backtest of the configuration above; every trade gets its state.
2. Per dimension, a bin is kept if its average R > 0 with at least 300 trades. A dimension gives a
   candidate filter only if it keeps at least one bin and drops at least one.
3. Candidates: every single-dimension filter, plus the intersection of the two single-dimension
   candidates with the highest discovery total R.
4. Lock the candidate with the highest discovery total R among those with at least 300 trades and an
   average of at least +0.05R. If none qualifies, the study fails at discovery and nothing is
   validated.

## Validation, once: [2025-09-29, 2026-09-29)

The locked filter is applied inside the backtest (a signal outside the kept bins is not taken, so
the position caps behave as live). Pass: at least 150 trades, average > +0.03R, profit factor > 1.05,
at least 3 of 4 quarters positive, and still positive with an extra 50% of each trade's spread.

## Live exam (later)

At the check-in, the 5m-learn live trades since 7 Oct 2026 that the locked filter would have kept
must average above 0R. Only a filter that passes both validation and the live exam may be proposed
as a demo forward test; nothing changes live from this study by itself.
