# Inverted 5m-learn (issue #69)

**Failed: inverting the 5m-learn signals loses more than the original.** No inverted variant was
positive in any period with recorded spreads (pre-registered rule in `INVERT_5M_PROTOCOL.md`).

## Recorded spreads (average R per trade, trades in brackets)

| Variant | P0 Jan–Sep 2024 | Y1 2024–25 | Y2 2025–26 | Pass |
|---|---:|---:|---:|---|
| original (5m-learn) | −0.169 (3,045) | −0.060 (4,199) | +0.001 (4,139) | – |
| mirror | −0.193 (3,594) | −0.147 (5,154) | −0.088 (4,921) | No |
| exact | −0.120 (3,394) | −0.089 (4,777) | −0.074 (4,824) | No |
| trend-keep | −0.192 (1,846) | −0.068 (2,507) | −0.047 (2,372) | No |

Exact wins 53–56% of trades, but each loss is 1.5× the win, so it still loses.

## Zero spread (diagnostic)

| Variant | P0 | Y1 | Y2 |
|---|---:|---:|---:|
| original | +0.015 | +0.053 | +0.057 |
| mirror | +0.007 | −0.048 | −0.031 |
| exact | −0.010 | −0.028 | −0.040 |
| trend-keep | −0.001 | +0.040 | +0.014 |

## Why

The original signals have a small positive edge before costs (+0.05R per trade in both recent
years). The spread, about 0.08R per side on these small stops, turns it into a loss. Inverting
throws away the small edge and still pays the spread, so it is worse in every period. The +20R the
inversion would have made on the 39 real closes (quick check in #69) came from flipping a losing
streak (−0.56R per trade, far below the long-run −0.03R), not from a pattern.

The useful lesson is the opposite of the idea: the 5m entries point the right way slightly more
often than not; the cost relative to the stop is the problem. That argues for wider stops or slower
timeframes (where the spread is a smaller share of the risk), not for flipping direction.
