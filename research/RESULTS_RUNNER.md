# Issue #96 results: 5m-v1 with runner exits — no variant passes

Protocol: `RUNNER_PROTOCOL.md` (committed first). Code: `runner_study.py`. Data: `results_runner/results.json`.
GOLD 5m, 8 Jan 2024 – 1 Oct 2026, live 5m-v1 limits (1 position, 6/day, flat Friday 20:45 UTC).

Fidelity: the replay of the fixed exit gives −0.021R/trade over 1,725 trades, compared with −0.007R over
1,717 trades in `run_backtest`. The 0.014R difference is within the 0.02R limit, so the study is valid.

| Exit | Trades | Win | Avg R | 2024 | 2025 | 2026 | +50% spread | Best trade | Helps | Tradable |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| B (fixed 3R, live) | 1725 | 25% | −0.021 | −0.176 | +0.128 | −0.002 | −0.064 | 3.0R | – | – |
| P1.5-T3 | 1542 | 39% | −0.001 | −0.128 | +0.068 | +0.082 | −0.047 | 8.1R | no | no |
| P1.5-T2 | 1665 | 38% | −0.033 | −0.139 | +0.061 | −0.011 | −0.074 | 6.4R | no | no |
| P3-T3 | 1462 | 25% | −0.004 | −0.171 | +0.125 | +0.060 | −0.047 | 8.9R | no | no |
| P1.5-EOD | 1550 | 38% | −0.003 | −0.142 | +0.060 | +0.108 | −0.046 | 15.7R | no | no |

- No runner exit beats the fixed 3R target in every year. All of them lose to it in 2025, the year of the
  steadiest rally, when the 3R target was hit most often.
- Taking half off at 1.5R raises the win rate from 25% to 38–39%. With the 3R trail or the end-of-day
  hold, the overall result improves from −0.021R to about breakeven (−0.001 to −0.003R), and 2024 and
  2026 are better. A 2R trail is too tight and makes it worse.
- None is an edge: every variant is negative with 50% more spread, and 2024 loses whatever the exit.
- Runners hold positions longer, so about 10% fewer signals are taken (one position at a time).

Conclusion: the runner idea gives bigger winners and roughly halves the bad years' damage, but it does not
turn 5m-v1 into a profitable strategy. Research only; no bots changed.
