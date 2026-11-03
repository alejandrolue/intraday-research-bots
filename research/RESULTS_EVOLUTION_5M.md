# 5m-learn V2: confluence and failed-break evolution

Issue: #58. The strategy is implemented but was not started. No account, order, live ledger, or
running bot was touched.

## Historical diagnostic

| Strategy | Year 1 N | Year 1 avg R | Year 2 N | Year 2 avg R | Combined N | Combined avg R | Total R |
|---|---:|---:|---:|---:|---:|---:|---:|
| Original `5m-learn` | 3,427 | -0.042 | 3,626 | -0.019 | 7,066 | -0.026 | -185.1 |
| `5m_evolution` | 48 | -0.062 | 43 | +0.186 | 91 | +0.055 | +5.0 |

The evolution is substantially more selective and historically better than the profile-matched
original, but it does **not** pass the pre-registered research-support rule. It lost in year one, produced only 91
trades, had 3/8 positive quarters, and did not have profitable longs and shorts in each year. Its
combined extra-0.20-point-cost result fell to only +0.010R/trade. Maximum drawdown was 14R.

## Which path contributed

| Setup path | Trades | Total R | Average R |
|---|---:|---:|---:|
| Original signal + zone confluence | 34 | -10.0 | -0.294 |
| Confirmed failed break / fakeout | 57 | +15.0 | +0.263 |

Combined longs made +18R over 54 trades; shorts lost -13R over 37. The year-two gain came from
longs (+21R), while year-two shorts lost -13R. This direction and regime dependence is the main
reason not to call the result a proven edge.

## Forward-test recommendation

The V2 is reasonable as a deliberately unproven demo execution study because it improves the
historical baseline and records each mode separately. Do not replace `5m-learn`, promote it to real
money, or tune it during the test. At roughly 0.9 historical trades per week, meaningful evidence
will accumulate slowly.

The prepared session is `5m-evolution` on the API-verified Capital.com demo account `5m learn V2`:
GOLD MINUTE_5,
MINUTE_30 trend, all hours, fixed exits, path recording, 11,000 starting balance, at most 2% risk per
trade, 10% daily-loss stop, 50 trades/day, five same-direction positions, and a 100-unit size cap.
Starting it remains a separate deployment action after review and merge.

Five simultaneous full-risk positions can expose roughly 10% of the risk ledger before losses are
realised. V2 counts current open stop-risk together with realised loss against the same 10% daily
budget; later entries are scaled or blocked. Existing sessions retain their prior risk behavior.

Capital.com's read-only account lookup reports CHF 11,000 for this account, matching the profile.
No account was switched or changed during verification.
