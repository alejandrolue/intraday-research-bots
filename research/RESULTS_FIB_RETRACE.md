# Issue #94 results: Fibonacci retracement continuation on hourly GOLD — FAILED

Protocol: `FIB_RETRACE_PROTOCOL.md` (committed before any result). Code: `fib_retrace.py`,
`fib_retrace_study.py`. Full training table: `results_fib_retrace/TRAINING.md`.

## Training 2019–2022: 0 of 18 eligible

Every variant lost money (average −0.04 to −0.17R/trade over 187–273 trades). None had more than
1 positive year out of 4, and every variant lost on both longs and shorts, even with gold in an
uptrend. Confluence (a round $50, a prior swing, or the EMA 50 in the 38–62% zone) made every level
and target worse rather than better. Shallow entries (38.2%) with the 1.272 extension target lost
the least. About a third of the trades ended because the hourly candle closed beyond 78.6%.

## Locked diagnostic, `fib0.382-any-ext`, validation 2023–2026 (run once)

| Trades | Win | Avg R | Total R | PF | Max DD | Long R (n) | Short R | Years | 2× spread | 6% financing |
|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| 245 | 47% | +0.035 | +8.5 | 1.08 | 10.8R | +21.6 (146) | −13.1 | 2023 +8.7, 2024 +6.3, 2025 −3.5, 2026 −3.0 | −0.001 | +0.025 |

Not a pass: the average is below +0.05R, only 2 of 4 years were positive, shorts lost, and doubled
spreads wipe it out. The small profit comes from longs in a strong gold bull market (the same
pattern as #19, #75 and #83), not from the Fibonacci levels.

## Conclusion

A trend-direction pullback to 38.2/50/61.8% on hourly gold has no edge after costs in this
implementation, with or without confluence. Nothing to deploy. Research only; no bots changed.
