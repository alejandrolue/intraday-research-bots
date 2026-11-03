# Issue #75: daily trend-following protocol

Written and committed before any return of these rules was computed. Research only: nothing here
changes a bot, an account or an order. Trading any market outside DEMO_EPICS/LAB_EPICS live would
need the user's approval.

## Markets and data

Capital.com daily candles (DAY), June 2007 – September 2026: GOLD, SILVER, OIL_CRUDE, US100,
US500, US30, DE40, UK100, J225, HK50, EURUSD. One rule set for all markets, long and short.

Many older candles have no recorded spread. For those days the spread is the market's median
recorded spread as a fraction of price (from the days that have one) × price × 2 (doubled, to be
conservative). Days with a recorded spread use it as recorded.

## Rules (`trend_daily.py`, 10 variants)

| Family | Variants | Entry | Exit |
|---|---|---|---|
| Donchian | 20/10, 55/20, 100/50 days × stop 2 or 3 ATR | close beyond the prior N-day high/low | close beyond the prior M-day opposite channel, or the stop |
| Moving average | 50/200, 20/100, stop 3 ATR | fast SMA above/below slow SMA | the opposite cross, or the stop |
| Time-series momentum | 126 or 252 days, stop 3 ATR | sign of the N-day close change | sign flip, or the stop |

Signals use day t's completed candle; orders fill at day t+1's open (buy at open + half spread,
sell at open − half spread). Initial stop k × ATR(20) from the fill, checked intraday on the
closing side of the quote; a gap through the stop fills at the open. After a stop, the always-in
rules (MA, momentum) wait for the next flip; Donchian waits for a new breakout. One position per
market. R = net points / initial stop distance.

Financing: 3% a year on the position value, for every calendar night held, long and short (the
first real fee implied about 1.8% a year on gold, so this is conservative). Stress: 6%.

## Periods

- Training: trades opened in [2008-01-01, 2017-01-01).
- Validation, used once: [2017-01-01, 2026-09-29).

## Rules for a pass

Training eligibility (portfolio of all 11 markets): ≥ 150 trades, average > +0.10R, profit factor
> 1.2, ≥ 6 of 9 calendar years positive, ≥ 6 of 11 markets positive, and still positive with 6%
financing. One variant per family is locked: the best eligible by R per year (then lower drawdown,
then name); if a family has none, its best by R per year with ≥ 150 trades is locked as a
diagnostic.

Validation pass (locked, eligible variants only): ≥ 150 trades, average > +0.05R, profit factor
> 1.1, ≥ 6 of 10 calendar years positive (2026 counts), ≥ 6 of 11 markets positive, still positive
with 6% financing and with every spread doubled again. Three families are validated, so one may
pass by luck: a pass leads to a demo forward test with small risk, not to real money.

Reported: all 10 training rows, per-market, per-side and per-year results, drawdown in R, the
locked choices with source hashes, and the validation with the stress tests.

## Amendment A1 (before any result)

Capital.com's daily history contains candles whose mid high is below another mid price (bid/ask
midpoints that do not line up; found on OIL_CRUDE while downloading). The loader now repairs such a
candle by setting high and low to the extremes of its open, high, low and close. The number of
repaired candles per market is reported.
