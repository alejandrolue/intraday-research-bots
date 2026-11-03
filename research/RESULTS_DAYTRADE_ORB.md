# Day-trading opening-range breakout (issue #85)

**DE40 passed as a new market; UK100 failed.** The #18 rule, unchanged, closed by the end of each
day (Jan 2024 – Sep 2026, recorded spreads, pre-registered in `DAYTRADE_ORB_PROTOCOL.md`).

| Market | Type | Trades | Win | Avg R | Total R | PF | +50% spread | 2024 / 2025 / 2026 | Pass |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| **DE40** | new | 407 | 36% | **+0.071** | +28.8 | 1.11 | +0.045 | +4.5 / +10.9 / +13.4 | **Yes** |
| UK100 | new | 364 | 28% | −0.176 | −63.9 | 0.76 | −0.224 | −27.4 / −39.4 / +2.9 | No |
| **US100** | re-check | 433 | 39% | **+0.078** | +33.6 | 1.13 | +0.058 | −4.9 / +35.7 / +2.7 | **Yes** |
| US500 | re-check | 476 | 34% | −0.014 | −6.7 | 0.98 | −0.046 | −23.3 / +17.2 / −0.5 | No |
| US30 | re-check | 378 | 34% | −0.058 | −22.0 | 0.91 | −0.073 | −12.1 / +8.3 / −18.2 | No |

The daily close mattered little: only 5–52 trades per market reached the flat time (most hit the
stop or target the same day). Without it the averages were DE40 +0.061R, US100 +0.098R, UK100
−0.168R, US500 −0.053R, US30 −0.055R.

## What it means

- **DE40 is real new evidence, within limits.** The rule was found on US100 and never tuned on DE40;
  there it is positive in all three years, with both sides positive (longs +22.9R, shorts +5.9R)
  and still positive with 50% more spread. But only one of the two new markets passed, so this is a
  candidate for a forward test, not proof.
- **US100 holds up with the daily close** (+0.078R), but it was used to find the rule, and it is
  carried mostly by 2025.
- **UK100, US500 and US30 do not work** with this rule: the opening breakout is not a universal edge.

A day-trading bot would trade DE40 at the Frankfurt open (09:00–17:15 Zurich) and US100 at the
New York open (15:30–21:45 Zurich), flat every evening. DE40 is outside `DEMO_EPICS`, so trading it
needs the user's approval.
