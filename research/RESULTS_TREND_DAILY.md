# Daily trend-following study (issue #75)

**Failed.** None of the 10 rules was eligible on 2008–2016, and all three locked rules lost on
2017–2026 across 11 markets after spreads and 3% financing. Nothing is deployed.

## Training, 2008–2016 (10 rules, 11 markets)

Best: `tsmom-126-k3` +0.020R per trade (921 trades), `donchian-55-20-k2` +0.017R (457). Every
rule was negative with 6% financing. The fast rules (20-day channel, 20/100 MA) lost clearly
(−0.09 to −0.18R). Full table: `results_trend_daily/TRAINING.md`.

## Validation, 2017 – Sep 2026 (checked once)

| Locked rule | Trades | Avg R | R/year | PF | Max DD R | Long R | Short R | 6% fin. | 2× spread | Pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| donchian-55-20-k2 | 551 | −0.037 | −2.1 | 0.95 | 93.8 | +111.8 | −132.2 | −0.172 | −0.052 | No |
| ma-20-100-k3 | 439 | −0.121 | −5.5 | 0.82 | 90.4 | +60.2 | −113.4 | −0.301 | −0.147 | No |
| tsmom-126-k3 | 1,119 | −0.038 | −4.4 | 0.88 | 108.3 | +77.5 | −120.0 | −0.111 | −0.051 | No |

(`ma-20-100-k3` was locked because the better `ma-50-200-k3` had only 135 training trades, below
the pre-registered 150.)

**By market** (validation), the same picture in all three rules: GOLD best (+27 to +33R), US100
positive in two of three; DE40, UK100 and EURUSD negative in all three.

## What it means

- **Longs made money, shorts lost** in every rule (longs +60 to +112R, shorts −113 to −132R over
  2017–2026). That is the long rise in gold and US stocks, not a trend-following edge: a rule that
  simply holds longs would show it too.
- **Financing is a large cost on daily holds.** At 3% a year a typical trade pays about 0.1R; at
  6% every rule loses clearly. Real index financing is about the benchmark rate plus 2–3%.
- **Classic trend following needs far more markets** (50–100 futures across rates, currencies and
  commodities) and futures-like costs to work. On 11 CFD markets with CFD financing it did not
  hold up here.

No rule from this study is promoted. Any follow-up (for example long-only gold or index trends)
would be a new hypothesis and would need its own pre-registered test; the long-side result above
is not a reason to deploy one.
