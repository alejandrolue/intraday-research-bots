# Zone-aware 5-minute Gold study

Issue: #56. Research only; no live bot, account, or order was changed.

## Result

No candidate passed the pre-registered protocol. The study tried 96 variants on the training year.
Zero were eligible, so one diagnostic per family was locked and tested unchanged on the holdout.

| Locked diagnostic | Train trades | Train avg R | Holdout trades | Holdout avg R | Combined trades | Combined avg R | Positive quarters | Result |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| EMA pullback + 1.0R zone room, w288/r3, London-NY | 652 | -0.079 | 639 | -0.061 | 1,291 | -0.070 | 2/8 | Fail |
| Zone rejection, 2R, w864/r6, London-NY | 439 | +0.004 | 368 | +0.060 | 807 | +0.030 | 4/8 | Fail |
| Breakout/retest, 2R, w288/r6, London-NY | 191 | +0.068 | 174 | -0.086 | 365 | -0.005 | 3/8 | Fail |

The complete training table is in `results_zone_decision_5m/TRAINING.md`; the JSON files preserve
the data audit, all 96 trials, locked selections, side splits, quarter results, cost sensitivity,
and holdout bootstrap intervals.

## Why they failed

- **EMA room filter:** negative in both years and on both sides. Avoiding the nearest opposing zone
  did not repair the original EMA signal's lack of 5-minute edge.
- **Rejection:** nearly flat in training, with only one positive quarter and losing shorts. It became
  positive in the holdout, but the profitable side flipped: training longs were +6.9R while holdout
  longs were -19.5R; holdout profit came from shorts (+41.7R). Its holdout 95% week-cluster bootstrap
  interval was -0.061R to +0.194R, which still includes a meaningful loss.
- **Breakout/retest:** the strongest training headline did not generalize. It changed from +0.068R
  to -0.086R per trade, all four holdout quarters were negative, and the extra 0.20-point cost test
  was negative in both years (-0.011R and -0.119R per trade).

Recorded median spread was 0.30 points. One crossed-spread candle in training was repaired using the
previous positive spread, as declared by the protocol. Backtests used recorded spreads, next-bar-open
fills, a 0.5R drift limit, fixed exits, and stop-first handling for ambiguous bars.

## Recommendation

Do not deploy any of these three zone-aware rules and do not fine-tune their thresholds on the
holdout. Keep `5m-learn` as an execution-data study only. The observed "bad entry at a zone" can be
useful as a trade-review label, but these results do not support turning it into an entry reversal or
an automatic opposing-zone filter.

A genuinely new follow-up should change the information set, not nudge these thresholds. The most
defensible next experiment is to classify market state before evaluating a zone: session/opening
regime, volatility expansion versus compression, and whether the zone is fresh or repeatedly tested.
That should be pre-registered on a new training/holdout split rather than derived from this holdout.
