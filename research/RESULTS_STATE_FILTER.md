# Market-state filter for the 5m-learn signals (issue #81)

**Failed at discovery.** On Jan 2024 – Sep 2025 (7,244 backtest trades of the live 5m-learn
configuration), no bin of any of the six market-state dimensions had a positive average after spread
with at least 300 trades. Per the pre-registered rule (`STATE_FILTER_PROTOCOL.md`), nothing was
locked and nothing was validated.

Baseline: −0.106R per trade, profit factor 0.84.

## Average R per trade by bin (trades in brackets)

| Dimension | Bins |
|---|---|
| session | tokyo −0.178 (2,297) · london −0.076 (1,944) · comex −0.128 (866) · us-data **+0.133 (229)** · ny-cash −0.069 (1,908) |
| ADX | < 20 −0.200 (2,661) · 20–30 −0.057 (2,336) · ≥ 30 −0.046 (2,247) |
| volatility percentile | < 0.3 −0.231 (1,852) · 0.3–0.7 −0.108 (2,724) · ≥ 0.7 −0.016 (2,668) |
| efficiency ratio | < 0.3 −0.125 (4,214) · 0.3–0.5 −0.073 (2,214) · ≥ 0.5 −0.094 (816) |
| state | sideways −0.205 (1,809) · mixed −0.130 (1,383) · breakout with −0.084 (997) · trend with −0.040 (2,941) · trend against −0.123 (114) |
| range position | late −0.116 (4,869) · middle −0.084 (2,368) · early −0.286 (7) |

The only positive bin, the US-data open (+0.133R), had 229 trades, below the 300 minimum; with that
few trades and six dimensions searched, it is most likely chance.

## What it shows

- **The live patterns of 7 Oct were chance.** London (+9.5R that day) averages −0.08R; strong trend
  and high volatility, which looked bad that day, are the least bad bins over 21 months.
- **These signals are trend signals.** They are worst in quiet, sideways markets (sideways −0.205R,
  ADX < 20 −0.200R, low volatility −0.231R) and least bad in strong trends and high volatility
  (−0.02 to −0.05R). No state makes them profitable after the spread.
- **For the user's idea** (fade the range in sideways markets, follow breakouts in trends): the
  filter route is closed for these signals. A sideways strategy would have to be a different signal
  (mean reversion), tested on its own; these trend signals should simply not be traded sideways.
