# Trade reviews

Post-mortems of individual live demo trades. Times are Zurich local time (CEST = UTC+2), UTC in
brackets. A review records what happened and **hypotheses to test later**; it never changes a live
strategy by itself (strategy changes need a pre-registered test, see #36 and #47).

## 2026-09-30 01:20 · 5m-learn · BUY GOLD · ema_pullback · −1.01R (−80.49 CHF)

| | |
| --- | --- |
| Signal candle | 01:15–01:20 (23:15 UTC), 5-minute, 30-minute trend filter BULLISH |
| Reason given | "Bullish EMA pullback resumed through the prior candle high" (prior high 4182.40) |
| Entry | signal 4182.72, filled 4182.70 at about 01:20:15 (no slippage) |
| Stop / target | 4180.01 / 4186.69: risk 2.69 pts (1.65 × ATR 1.61), target 1.5R |
| Size | 35.47 units, 1% risk (95 CHF budget) |
| Exit | stop at 01:22:31, filled 4179.98 (0.03 slippage) |
| Price path | best −0.13R, worst −0.68R before the stop; never in profit (8 samples, 15 s apart) |
| After the stop | low 4179.97 one minute later, then back to 4181.0 by 01:24 |

**Execution was fine; the entry decision was the problem.** Fill and stop slippage were a few
hundredths of a point. What made it a poor trade, in order of weight:

1. **The signal candle was a rejection, not a breakout.** It traded to 4183.79 and closed at 4182.72,
   giving up 1.07 of its 1.56 range: sellers were already pushing it down when the bot bought "the
   breakout". The next 1-minute candles made lower highs from 01:18 on.
2. **Buying into resistance with the target behind it.** Gold had been rejected at 4185.3 (22:30–23:00)
   and 4185.2 (00:00, right after the daily reopen), then sold off to 4176.8. The target (4186.69)
   needed a break of both highs; the entry sat inside the 4181–4183 zone where the 00:05–00:25 selling
   began.
3. **Stop at an obvious level, too tight for the conditions.** 2.69 pts, just under the prior
   candle's low (4180.45) and the round 4180. The move that stopped it reached 4179.97, 0.04 below the
   stop, then reversed. The 0.50 spread cost 19% of the risk at entry (the trade started at −0.19R).
4. **Weak trend, thin hours.** The 5-minute EMAs were only 1.3 pts apart (< 1 ATR), and the two
   previous 30-minute candles were bearish; the "bullish" 30-minute bias came from the 22:30 rally the
   evening before. It was 1 h 20 after the daily reopen, with a low ATR (1.6), so moves were mostly noise.
   5m-learn trades all hours by design.
5. **Third stacked long, at the highest price.** Two BUYs were already open (4177.29 from 21:55, and
   4180.93 from 01:05), so this one used the last of the 3 slots for the same idea. The same setup had
   been rejected one candle earlier (01:10, reward-to-risk below 1.5) and was taken at a worse price
   one candle later.

**Hypotheses to test** (backtest on year 1, validate on a held-out sample; do not change 5m-learn live):

- H1 Skip a breakout entry when the signal candle closes in the lower half of its range (rejection wick).
- H2 Require the spread to be at most 10% of the stop distance.
- H3 Require at least 1R of room between entry and the nearest recent swing high (low for shorts).
- H4 No new same-direction entry above (below, for shorts) the last open entry within 60 minutes.
- H5 No entries in the first 2 hours after the daily reopen (00:00–02:00 Zurich).

One trade proves nothing on its own: this is the known loss pattern of these strategies (see the
2-year backtests in AGENTS.md: the spread relative to a small stop makes 5-minute entries lose).
Collect the same fields for every 5m-learn loss, then test H1–H5 together in the #36 study.

## 2026-09-30 01:10 · 5m-learn · BUY GOLD · ema_pullback · +1.51R (+119.17 CHF)

| | |
| --- | --- |
| Signal candle | 01:05–01:10 (23:05 UTC), 5-minute, 30-minute trend filter BULLISH |
| Reason given | "Bullish EMA pullback resumed through the prior candle high" (prior high 4180.83) |
| Entry | signal 4181.07, filled 4180.93 at about 01:10:30 (0.14 better; stop and target shifted by the same gap) |
| Stop / target | 4178.40 / 4184.72: risk 2.53 pts (1.35 × ATR 1.88), target 1.5R |
| Size | 37.49 units, 1% risk (95 CHF budget) |
| Exit | target at 01:49:14, filled 4184.74 |
| Price path | +0.81R at 01:19 → −0.92R at 01:32:44 (bid 4178.61, **0.21 pts above the stop**) → target in the 01:45 candle (149 samples) |

**A better entry than the 01:20 loser, but it survived partly by luck.**

What was better:
1. **A real breakout candle.** 01:05 closed at 4181.07, near its high of 4181.28 (top 14% of its range), above the prior high. The loser's signal candle had closed in its lower half after a rejection.
2. **Early in the move, below resistance.** It bought the first push up from the 00:35 low (4176.8), under the 4181–4183 zone. The target, 4184.72, sat just inside the 4185.2 highs (1.7R of room), so it did not need a breakout of the highs.
3. **Positive fill.** It filled 0.14 better than the signal close.

What was the same as the loser:
- **Same tight stop and spread cost.** 2.53 pts of risk with a 0.50 spread: the spread cost 20% of the risk at entry.
- **Same weak trend and thin hours.** The EMAs were only 0.9 pts apart (< 1 ATR), 1 h after the daily reopen, and this was the second stacked long.
- **It nearly lost.** The 01:30 dip (the same move that stopped the 01:20 trade) came within 0.21 pts, 0.08R, of its stop. One more tick of spread or noise and both trades would have lost 1R.

**Check of the hypotheses from the first review against both trades:**

| Hypothesis | 01:10 winner | 01:20 loser | Separates them? |
| --- | --- | --- | --- |
| H1 Skip if the signal candle closes in its lower half | taken | skipped | yes |
| H2 Spread ≤ 10% of the stop | skipped (20%) | skipped (19%) | no: blocks both |
| H3 ≥ 1R room to the nearest swing high | taken (1.7R) | skipped (0.9R to 4185.2) | yes |
| H4 No same-direction entry above the last one within 60 min | taken (last entry 3 h before) | skipped | yes |
| H5 No entries 00:00–02:00 Zurich | skipped | skipped | no: blocks both |

H1, H3 and H4 would have kept the winner and skipped the loser; H2 and H5 would have skipped both.
Two trades cannot validate anything: this table only records which hypotheses are worth testing
first in #36. The near-stop (0.08R) also says the stop placement itself (H2, a wider stop at
smaller size) deserves its own test.
