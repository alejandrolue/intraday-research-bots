# Issue #23: Asia open protocol (portfolio part 2)

Written before any candidate returns were evaluated. Research only. Same design as issue #18
(`INDEX_OPEN_PROTOCOL.md`) and #12, including amendment A1 (crossed-quote spreads replaced by
the previous candle's spread and listed), with the differences stated here.

## Markets and sessions

| Market | Session | Local open | Extra-cost sensitivity per trade |
|---|---|---|---|
| J225 (Japan 225) | `tokyo` | 09:00 Asia/Tokyo | 5.0 index points (≈ half its spread) |
| GOLD | `tokyo` | 09:00 Asia/Tokyo | 0.25 price points (≈ half its spread) |
| HK50 (Hang Seng) | `hongkong` | 09:30 Asia/Hong_Kong | 2.5 index points (≈ half its spread) |

Neither zone observes daylight saving. Days whose opening range is incomplete (holidays,
missing candles) are skipped by the strategy, as before.

## Data and execution

- Cached Capital.com MINUTE_5 candles plus HOUR_4 of the same market for the trend filter.
  Training [2024-09-29, 2025-09-29), holdout [2025-09-29, 2026-09-29); each year starts flat
  with 300 warm-up bars; open positions close at year end.
- `run_backtest(..., strategies=[candidate])`: recorded spreads, next-open fills, stop-first
  on ambiguous candles, 0.5R drift limit. One position, at most three entries per UTC day,
  fixed stop and target, fixed 0.1 units, R-based results.
- **Difference 1:** the London–New York entry window is switched off (`window=None`), because
  it would block every Asian-session trade. Entries are limited only by each strategy's own
  window: from the end of the opening range until 175 minutes after the open.
- **Difference 2:** as in #18, the absolute spread cap is disabled; spread costs are still
  charged on every fill.

## Finite grid: 72 variants

Three markets × three families (`orb`, `rolling_sweep` and a third family that is not published) × two range lengths
(15 or 30 minutes) × two rewards (1.6 or 2.0) × two trend filters (none or HOUR_4).

## Selection and acceptance

Identical to #18, per market: rank each (market, family) group on year 1 only; eligible:
≥150 trades, average R > +0.05, positive long AND short total R, ≥3 of 4 positive quarters;
lock the best eligible (else the best with ≥150 trades as a diagnostic, else the most-traded).
That locks **nine** variants, committed before year 2 is evaluated, then validated once.

A research pass requires training eligibility, > +0.05R/trade in each year and combined,
≥300 combined trades, ≥5 of 8 positive quarters, and positive long AND short R in each year.

Nine locked candidates is more multiple testing than #18 (six) or #12 (three); any pass is a
candidate for a demo forward test, not proof of an edge.
