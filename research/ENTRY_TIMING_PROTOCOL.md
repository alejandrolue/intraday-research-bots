# Issue #92: entry timing, cooldown and target extension for the 5m-learn signals

Written and committed before any backtest of these variants. Seen beforehand (not used for any
choice beyond naming the candidates): the live counterfactuals posted in #92 (138 live closes,
29 Sep – 9 Oct) and the 9 Oct trade review (losing streaks; the target extension helped while
breakeven/trailing cut winners).

## Signals (unchanged, the live 5m-learn configuration)

GOLD MINUTE_5, the three original strategies, MINUTE_30 trend filter (EMA 20/50), reward-to-risk
≥ 1.5, spread ≤ 0.80, 499-candle windows, all hours. A signal is generated at the 5m close; the
stop and target are the strategy's.

## Execution on 1-minute candles (`entry_timing.py`)

Recorded spreads; a long fills at the ask and exits at the bid, a short the reverse. Stop first in
an ambiguous 1m candle. At most 3 positions plus pending orders, all in one direction; at most
50 entries per UTC day; flat at Friday 20:45 UTC with no entries until the next week opens.

## Variants (12): entry × cooldown × exit

- **Entry**
  - `market`: fill at the first 1m open at or after the signal close; skipped if it moved more
    than 0.5R from the signal close (the live drift guard).
  - `L50`: limit at 50% of the signal candle, valid 15 minutes; fills only if price trades through
    it by the spread; cancelled if the stop is traded first.
  - `M1`: within 15 minutes, wait for a 1m pullback of ≥ 0.3 × ATR5 against the trade, then a 1m
    close beyond the previous 1m high (long) / low (short); fill at the next 1m open (drift guard
    as above); cancelled if the stop is traded first.
- **Cooldown:** `off` / `on`. After 2 consecutive stops in one direction, no new signal in that
  direction for 120 minutes.
- **Exit:** `fixed` / `extend`. When price comes within 0.25R of the target, the target moves 1R
  further and the stop moves to 1R below the old target (a 1.5R target becomes 2.5R with the stop at
  +0.5R), as often as it happens. There is no earlier breakeven or trailing, so winners are not cut
  before they reach their original target.

The original stop and target levels are kept for every entry; R is measured on each fill's own
risk.

## Periods and rules

- Y1 (selection): signals in [2024-09-29, 2025-09-29). Y2 (validation, once): [2025-09-29, 2026-09-29).
- Baseline: `market / off / fixed`.
- Eligible on Y1: average R > 0 after spread, profit factor > 1.1, ≥ 200 filled trades, and total R
  per signal at least +0.05R better than the baseline. Lock the (up to) two best eligible by Y1 total
  R; if none is eligible, lock the best by Y1 total R as a diagnostic (the study fails).
- Pass (Y2): the same four conditions on Y2.
- Sanity check, not a selection input: the baseline replayed over the live window (29 Sep 17:40 UTC
  – 9 Oct 2026) compared with the live 5m-learn closes.
