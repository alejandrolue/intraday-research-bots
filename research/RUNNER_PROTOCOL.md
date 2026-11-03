# Issue #96: 5m-v1 runner exits (half at the target, the rest runs)

Written and committed before any result of these exits was computed. Research only.

## Signal (unchanged, the live `5m-v1`)

`evp-pullback-off-rr3` on GOLD 5-minute candles, 30-minute EMA21 bias (`ema21_spec`), 1000-candle
window, all hours, original 3R target, entry at the next candle's open with the usual drift shift and
the 0.5R drift block, spread gate 0.80. The full signal stream comes from `run_backtest` with no
position or daily limits. A replay then applies the live limits: one position at a time (a runner
still open blocks new signals), at most 6 entries per day, flat at Friday 20:45 UTC.

## Exits (5 variants, 5-minute candles, exit side of the spread, stop first in an ambiguous candle)

- `B`: fixed stop and 3R target (the live bot).
- `P1.5-T3`: half closes at +1.5R; the runner's stop moves to the entry and then trails 3R behind the
  best price reached (updated at each candle close); no target.
- `P1.5-T2`: the same with a 2R trail.
- `P3-T3`: half closes at +3R (the live target); runner as in `P1.5-T3`.
- `P1.5-EOD`: half closes at +1.5R; the runner's stop moves to the entry and it closes at the 20:55 UTC
  candle's close (before the daily break).

R is per whole position: 0.5 × (R of the first half) + 0.5 × (R of the runner).

## Periods and pass rules

Years: 2024 (from 8 Jan), 2025, 2026 (to 1 Oct).

- A runner exit **helps** if its average R per trade beats `B` in each of the three years.
- It **makes 5m-v1 tradable** if it helps, its overall average is > +0.05R over ≥ 300 trades, at least
  2 of 3 years are positive, and it is still positive with 50% more spread on entry.

Fidelity check: the replay of `B` must match `run_backtest` with the live limits (average R within
0.02R). If not, the study is reported as not valid.
