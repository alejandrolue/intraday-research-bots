# Issue #94: Fibonacci retracement continuation on hourly GOLD

Written and committed before any result of these rules was computed. Research only.

## Rules (`fib_retrace.py`, 18 variants)

- **Leg:** a confirmed swing low followed by a confirmed swing high (long), or the reverse (short).
  Pivots with 3 candles on each side, usable only after those 3 candles closed. At least 3 × ATR(14)
  long.
- **Trend:** the 4-hour bias (EMA 20/50 filter, from completed 4h candles built out of the hourly
  ones) must agree when the leg end is confirmed.
- **Entry:** a limit at the Fibonacci level (38.2%, 50% or 61.8% of the leg), placed when the leg end
  is confirmed, if price is still above it (long). It fills when price trades through it by the spread.
  It is cancelled after 48 candles, when price makes a new extreme beyond the leg end, or when price
  passes 78.6% without a fill.
- **Confluence (variant):** the 38.2–61.8% zone (±0.1 ATR) contains a round $50 level, a prior swing
  from the 200 candles before the leg, or the hourly EMA 50. Without the confluence variant, any leg
  qualifies.
- **Exit:**
  - hard stop at the swing start ± 0.1 ATR;
  - exit at the close of a candle beyond 78.6% (the leg failed);
  - target per variant: the leg end (`high`), the 1.272 extension (`ext`), or 2R (`2R`);
  - time exit after 120 candles.
- The fill candle counts in full: a stop or a failed close in the same candle closes the trade.
- **Costs:** recorded spread (candles without one: 2× the median recorded spread as a fraction of
  price); financing 3% a year per calendar night (stress 6%). One position at a time.

## Periods

Training: fills in [2019-01-01, 2023-01-01). Validation, once: [2023-01-01, 2026-09-29).

## Pass rules

Gold rose for most of 2019–2026, so the long and short sides are both required not to lose.

Training eligibility: ≥ 100 trades, average > +0.10R, profit factor > 1.2, ≥ 3 of 4 years positive,
long and short totals each ≥ 0, still positive with doubled spreads. Lock the (up to) two best eligible
by total R; if none is eligible, lock the best by average R with ≥ 100 trades as a diagnostic (the
study fails).

Validation pass: ≥ 80 trades, average > +0.05R, profit factor > 1.1, ≥ 3 of 4 years positive
(2023–2026), long and short totals each ≥ 0, still positive with doubled spreads and with 6% financing.
