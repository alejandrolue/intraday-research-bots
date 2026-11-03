# intraday-research-bots

[![tests](https://github.com/alejandrolue/intraday-research-bots/actions/workflows/tests.yml/badge.svg)](https://github.com/alejandrolue/intraday-research-bots/actions/workflows/tests.yml)

Intraday trading bots for gold and US indices, with a backtester, broker execution on Capital.com **demo** accounts,
and the full research log: rules registered before testing and locked out-of-sample exams.

> **Not financial advice.** This is a research project. Every bot here runs on demo accounts. Backtests are
> optimistic by nature, and some of the rules below were chosen on the same years they are shown on. Trading CFDs
> with leverage can lose more than you expect. Use it to learn, not to copy trades.

## What's in here

- **A small trading engine** (`src/goldbot/`): strategies, risk-based position sizing, daily loss limits, broker
  reconciliation against Capital.com's real fills, stop-order brackets for range breakouts, weekend and end-of-day
  flat guards, Telegram trade messages and commands.
- **A backtester** that uses the same strategy and trade-management code as the live bots, with real bid/ask
  spreads, next-bar fills and stop-first handling when a bar touches both stop and target.
- **The rules that survived**, each running live on demo as its own bot, plus a combined day-trading portfolio.
- **The research log** (`research/`): one protocol and one results file per study.

## How the research works

Most trading ideas look good on the data they were invented on. The method here is built to catch that:

1. **Write the rules first.** Each study's protocol (variants, train/test split, pass criteria) is fixed *before*
   any result is seen.
2. **Train, then one locked exam.** Variants are screened on the earlier period. At most a few are locked and run
   **once** on unseen data. No re-tuning after the exam.
3. **Realistic costs.** Recorded spreads, extra-cost and slippage stress tests, bootstrap confidence intervals.
4. **Both sides and every year.** A rule must work long *and* short and not depend on one lucky period. Many
   "edges" in gold turned out to be the 2024–2026 bull market.
5. **Forward test on demo** with evaluation rules registered before the bot starts, and no tuning while it runs.
6. **Check live against backtest.** The backtester reproduced 36 of 39 live trades of one bot, with the same
   outcome in 35 of 36 (`research/LIVE_VS_BACKTEST_5M_LEARN.md`).

## Rules that passed their exam

Exam period 2025-01 → 2026-09 on 5-minute or 1-minute candles, recorded spreads. R = the trade's initial risk
(entry to stop), so +0.10R means 10% of the risk gained per trade on average.

| Rule | Market | Idea | Exam result |
|---|---|---|---|
| **US100 opening range** | US100 | Break of the first 15 minutes of the cash session, with the 4-hour trend, target 2R | passed; +0.15R on 2025–26 in the portfolio run |
| **US100 / US500 1-minute ORB** | US100, US500 | Opening-range breakouts on 1-minute candles, target 1–1.5R | +0.09R / +0.06R on the holdout |
| **Gold London breakout** | Gold | Range 00:00–07:00 London, broken 07:00–12:00 via stop orders, flat at 16:00 | 335 trades, **+0.08R**, PF 1.27 |
| DE40 opening range | DE40 | Same opening-range rule as US100 | +0.07R, every year positive (not run live) |

Three more rules are **kept private** (code, settings and research not published): one on US100 (exam +0.18R per
trade), one on gold (exam +0.07R) and a gold rule in the lab forward test. Their live numbers are in the table below.

**Combined as one portfolio**, including private rules A and B (1% risk per trade, −2% daily loss limit,
2025-01 → 2026-09): +151%, max drawdown 17.5%, 17 of 21 months up. Read that as an upper bound: the rules were selected on this period.

## Live demo results

Every rule above runs as its own bot on a Capital.com demo account, and the combined day-trading portfolio runs on
another. The lab is a separate demo account where variants and experiments run side by side. All rules are shown,
winners and losers. This table is updated about once a month.

<!-- live-results:start -->
_Updated 3 Nov 2026. R = the trade's initial risk; spread and slippage are included because these are real demo fills._

| Rule | Running since | Days | Trades | Won | Total R | Avg R | Backtest exam avg R |
|---|---|---|---|---|---|---|---|
| US100 opening range | 29 Sep 2026 | 35 | 9 | 5 | +5.97 | +0.66 | +0.15 |
| Private rule A (US100) | 12 Oct 2026 | 22 | 1 | 1 | +1.89 | +1.89 | +0.18 |
| US100 1-minute ORB | 12 Oct 2026 | 22 | 16 | 6 | -0.87 | -0.05 | +0.09 |
| US500 1-minute ORB | 12 Oct 2026 | 22 | 15 | 6 | -3.01 | -0.20 | +0.06 |
| Gold London breakout | 12 Oct 2026 | 22 | 10 | 2 | -2.29 | -0.23 | +0.08 |
| Private rule B (gold) | 27 Oct 2026 | 6 | 1 | 1 | +1.40 | +1.40 | +0.07 |
| Day-trading portfolio (all rules incl. private A and B, one account) | 27 Oct 2026 | 7 | 15 | 6 | +2.23 | +0.15 | — |
| Lab: US100 opening range, 2R target | 29 Sep 2026 | 35 | 9 | 5 | +6.06 | +0.67 | — |
| Lab: US100 opening range, 1.6R target | 29 Sep 2026 | 35 | 9 | 5 | +3.86 | +0.43 | — |
| Lab: private rule C (gold) | 29 Sep 2026 | 35 | 8 | 4 | +2.38 | +0.30 | — |
| Lab: ML lines, combined (private, code not included) | 2 Nov 2026 | 0 | 4 | 2 | -0.09 | -0.02 | — |
<!-- live-results:end -->

Read these with care: 1–16 trades per rule is far too few to confirm or reject a backtest. The evaluation rules for
each forward test were fixed before it started; the first full review is at the end of January 2027. Some days are
missing because the computer running the bots went to sleep; open positions stayed protected by broker-side stops.

## Machine learning track (private)

The ML work runs alongside the rule-based bots but is kept private while it is being tested. What it looks like:

- **The task:** every hour during the London and New York sessions, predict whether gold reaches +1 ATR or −1 ATR
  first (a triple-barrier label). Each prediction can be scored as a trade with a fixed stop and target.
- **Models:** gradient boosting and logistic regression, trained on years of hourly history with walk-forward
  splits only, so no model ever sees the period it is judged on.
- **Self-training:** once a month a new model is fitted and scored on the last three months it has not seen. It
  only replaces the current champion if it clears a gate fixed in advance (AUC, trade count, average R after costs,
  and beating the champion).
- **A tournament of challengers:** variants with different inputs and training windows (price-only, recent-data,
  chart-pattern features) predict next to the champion. Every weekend a new version is also fitted on recent
  months; only the best ones survive each round.
- **Shadow first, then demo:** every line logs its predictions before the outcome is known, so its live record
  can't be edited afterwards. The most promising lines also trade on demo accounts with real fills, spreads and
  slippage, so shadow results and real execution can be compared trade by trade.
- **A rule for "it works", written before the data came in:** a line has to collect 300 live predictions and beat
  the champion by a set margin on AUC and on average R after costs. Lines that fall below a coin flip are removed.
- **Things already tested and dropped:** a pretrained time-series foundation model, news sentiment as an input,
  volatility prediction (the clock and the ATR already explain it), and meta-labeling filters on the winning rules.

**Status:** the historical walk-forward tests found no reliable edge, but some lines are ahead in the live test so
far. The samples are still too small to tell skill from luck; the first checkpoint is at 300 live predictions per
line. The code, features and models are not part of this repo.

## Running it

Requires Python 3.11+ and a Capital.com **demo** account with an API key.

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env        # fill in your demo API key, login and password
.venv/bin/pytest -q
```

```bash
.venv/bin/goldbot search Gold                       # find the market's epic
.venv/bin/goldbot backtest --days 365 --compare     # backtest on downloaded history (cached under data/)
.venv/bin/goldbot watch --paper                     # run a bot on a local paper ledger
.venv/bin/goldbot daytrade --check                  # validate the day-trading portfolio roster
```

The API client only connects to Capital.com's demo host. Broker orders are only sent with `--demo-execute`
together with `CAPITAL_DEMO_EXECUTION_ENABLED=true`. Put your own demo account ID in `daytrade.json` and
`lab.json`. Price history is downloaded from your own account and never committed.

## How it was built

Built by Alejandro Lüthi with two AI coding agents, Claude Code and Codex, working through issues, branches and
pull requests and reviewing each other's work. Issue numbers in the research files (#18, #31, …) refer to the
project's private tracker.

## License

MIT, see `LICENSE`.
