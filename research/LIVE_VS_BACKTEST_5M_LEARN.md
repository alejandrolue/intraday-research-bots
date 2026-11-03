# Live vs backtest: 5m-learn (issue #47)

**Verdict: the backtester is realistic, if anything slightly pessimistic.** Trades, entries and
outcomes match the live demo closely; the differences come from operations (Mac asleep, weekend
guard, open-position caps), not from the simulation.

Window: 29 Sep 19:40 – 7 Oct 00:40 Zurich (17:40 UTC, when 5m-learn switched to the 30m trend, to
6 Oct 22:40 UTC). Backtest: the exact live configuration (three original strategies, MINUTE_30
filter, all hours, fixed exits, 3 positions, 50 trades/day, 499-candle windows), recorded spreads,
next-candle-open fills. The duplicate SELL from the 29 Sep restart bug is outside the window.

| | Live | Backtest |
|---|---:|---:|
| Trades | 39 | 83 (33 while the Mac slept) |
| Total R | −21.8 | −19.5 (−32.5 excluding the sleep) |

## Trade by trade

- **36 of 39 live trades are in the backtest** with the same signal candle, direction and strategy.
  **35 of the 36 had the same outcome.** Matched R: live −21.3, backtest −23.5.
- **Entries:** live fills averaged 0.046R *better* than the backtest's next-open fill (median
  0.061R better, worst 0.24R worse). The live drift guard blocked 2 entries that had moved more than
  0.5R.
- **The one different outcome** (30 Sep 01:10 Zurich, BUY ema_pullback): it won live (+1.51R) and
  lost in the backtest (−1.0R). The live entry was better and the stop was not touched by the
  broker's bid; the candle-based simulation crossed it.
- **3 live-only trades:** one is a candle apart from a backtest trade (1 Oct 21:20 vs 21:15 Zurich);
  the others follow from different open positions at the time.
- **47 backtest-only trades:** 33 fall in the Mac's sleep (5 Oct 01:15 – 6 Oct 22:55 Zurich; the
  backtest made +13R there). Of the other 14, 2 are the Friday last-hour block (weekend guard) and
  the rest follow from the 3-position cap and daily path differences (a different set of open
  positions changes which later signals are allowed). 12 of those 14 lost.

## What this means

- Backtest results for 5m strategies can be trusted at the trade level; the live demo did not reveal
  hidden costs or fill problems beyond the recorded spread.
- The live losses are the strategy, not the execution: 5m-learn lost because the signals do, as the
  backtests predicted (−0.06R/trade in 2024–25).
- Gaps matter: a sleeping Mac skipped 33 trades. The power warnings from #70 now cover that.

Reproduce: the script in the issue thread uses only cached candles and the live journal.
