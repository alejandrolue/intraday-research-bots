# Entry timing, cooldown and target extension for the 5m-learn signals (issue #92)

**Failed: no variant was eligible.** On the same 5m-learn signals (6,569 in Y1, 6,436 in Y2),
executed on 1-minute candles with recorded spreads. Pre-registered in `ENTRY_TIMING_PROTOCOL.md`.

## R per signal (total R / signals), by variant

| Variant | Y1 2024–25 | Y2 2025–26 | Live replay 29 Sep – 9 Oct |
|---|---:|---:|---:|
| market-nocool-fixed (= live 5m-learn) | −0.049 | −0.005 | −0.129 |
| **market-nocool-extend** | −0.030 | **+0.025** (PF 1.07, +160R) | −0.182 |
| market-cool-extend | −0.031 | +0.015 | −0.146 |
| market-cool-fixed | −0.044 | −0.018 | −0.055 |
| M1-nocool-fixed | −0.031 | −0.017 | −0.105 |
| M1-nocool-extend | −0.022 | −0.009 | −0.113 |
| M1-cool-fixed | −0.026 | −0.018 | −0.110 |
| M1-cool-extend (locked diagnostic) | **−0.019** | −0.003 | −0.127 |
| L50 (all four) | −0.058 to −0.071 | −0.017 to −0.037 | −0.158 to −0.233 |

## What it shows

- **The limit pullback (L50) is clearly worse, in every period.** Its win rate falls to 22–25%:
  the limit fills mostly on trades that are failing, and the strong winners never come back. This is
  the trap the issue warned about, now measured.
- **The 1-minute confirmation (M1) helps a little in Y1** (−0.049 → −0.031R per signal) **and not in
  Y2** (−0.005 → −0.017). On the live days its replay was only slightly better than market. It
  avoids some fast losers but misses about a third of the signals, including winners. Not robust.
- **Target extension with a profit lock is the only change that helped in both years** (+0.019R
  per signal in Y1, +0.030R in Y2; Y2 itself turned slightly positive). It lost on the 10 live days,
  though, and it is still negative in Y1. Promising, not proven.
- **The cooldown after 2 stops has no consistent effect:** it helps some variants and hurts others.
- **Nothing reaches the bar** (positive in both years, PF > 1.1, +0.05R per signal over the baseline).
  The entry is not where the 5m signals lose. They lose on cost against a very small edge, and no
  execution trick here adds enough.

## Live replay vs live trading

The baseline replay over 29 Sep – 9 Oct (154 trades, −24.1R) includes the period when the Mac was
asleep (5–6 Oct), which the live bot missed. Trade-level fidelity was checked earlier in #47
(36/39 live trades matched).
