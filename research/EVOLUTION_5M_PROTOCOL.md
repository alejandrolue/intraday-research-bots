# Issue #58: 5m-learn evolution protocol

Written before evaluating returns. The two-year history has already been used by earlier studies,
so these results are retrospective diagnostics, not an untouched holdout or proof of future edge.

## Fixed strategy

`5m_evolution` keeps the original `5m-learn` setup set and execution model but exposes only two
entry paths:

1. **Confluence:** an original breakout/retest, EMA pullback, or demand/supply rejection is READY;
   its two signal candles interact with an intact same-side swing zone; the close recovers the zone;
   and the nearest intact opposing zone leaves at least 2R after the structural stop.
2. **Fakeout:** a confirmed swing zone is breached by a wick and reclaimed, or closed through and
   reclaimed on the next candle. A second close confirms beyond the failed-break candle. The nearest
   opposing zone must leave at least 2R.

Zones use a 288-bar window, radius-6 confirmed swings, 0.25 ATR clustering and width, 0.10 ATR
interaction buffer, and a 0.25 ATR stop buffer. The rules are symmetric for longs and shorts. The
existing external MINUTE_30 trend filter still must agree.

## Comparison

- Data: GOLD MINUTE_5, 2024-09-29 through 2026-09-29.
- Baseline: the original three `5m-learn` strategies.
- Candidate: the single combined `5m_evolution` strategy above.
- Both: MINUTE_30 trend, all hours, fixed exits, 50 trades/day, recorded spread, next-bar-open
  fills, 0.5R drift limit, and stop-first ambiguous bars. The original baseline keeps its live cap
  of three same-direction positions; the candidate uses the user's later-specified cap of five.
- Risk-profile simulation: 11,000 starting balance, maximum 2% risk/trade, 10% daily-loss stop,
  and 100-unit size cap. For V2, realised daily loss plus current open stop-risk cannot exceed the
  10% daily budget; later entries are scaled or blocked. R results remain the primary comparison.
- Report each year, every quarter, long/short split, setup-mode split, and an extra 0.20-point cost.

No parameter grid or post-result threshold changes are allowed in this issue. Research support for a
profitability claim requires more than +0.05R/trade in each year, at least 300 combined trades, both
sides positive in each year, and at least 5/8 positive quarters. Regardless of this retrospective
result, a user-approved demo execution study must be described as unproven and evaluated prospectively.

## Deployment gate

This issue does not start a bot. A dedicated demo session may be wired and started only after review,
after the user supplies the new Capital.com demo account name/ID, and after explicit start approval.
The intended profile is: `5m-evolution`, GOLD MINUTE_5, MINUTE_30 trend, all hours, fixed exits,
price-path recording, 11,000 starting balance, 2% maximum risk/trade, 10% daily stop, 50 trades/day,
five same-direction positions, and 100-unit size cap. Five full-risk positions can expose roughly
10% of the risk ledger before any loss is realised. Open stop-risk and realised loss share that same
10% budget, so the profile cannot add another full set of risk after losses.
