"""Issue #96: 5m-v1 signals with runner exits (half at the target, the rest trails)."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goldbot.backtest import BacktestConfig, run_backtest  # noqa: E402
from goldbot.history import _read  # noqa: E402
from goldbot.risk import RiskConfig  # noqa: E402
from goldbot.volume_pullback import VolumePullbackConfig, VolumePullbackStrategy  # noqa: E402

OUT = ROOT / "research/results_runner"
PROTOCOL = ROOT / "research/RUNNER_PROTOCOL.md"
START, END = datetime(2024, 1, 8, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc)
WINDOW = 999
EXTRA_SPREAD_SHARE = 0.5


@dataclass(frozen=True)
class Exit:
    id: str
    partial_r: float | None   # None: fixed 3R
    trail_r: float | None     # None with a partial: hold to the end of the day
    target_r: float = 3.0


EXITS = (Exit("B", None, None), Exit("P1.5-T3", 1.5, 3.0), Exit("P1.5-T2", 1.5, 2.0),
         Exit("P3-T3", 3.0, 3.0), Exit("P1.5-EOD", 1.5, None))


@dataclass(frozen=True)
class Signal:
    opened_at: datetime
    direction: int
    entry: float
    stop: float


def load(resolution: str) -> list:
    rows = {}
    for p in sorted((ROOT / "data/history/GOLD" / resolution).glob("*.csv")):
        for b in _read(p):
            if b.timestamp < END:
                rows[b.timestamp] = b
    bars = [rows[k] for k in sorted(rows)]
    for i, b in enumerate(bars):
        if b.spread <= 0 and i:
            bars[i] = replace(b, spread=bars[i - 1].spread)
    return bars


def backtest(m5, m30, *, limits: bool):
    before = [b for b in m5 if b.timestamp < START][-(WINDOW + 10):]
    candles = before + [b for b in m5 if b.timestamp >= START]
    risk = RiskConfig(max_positions=1, max_trades_per_day=6) if limits else \
        RiskConfig(max_positions=10_000, max_trades_per_day=10_000, max_daily_loss=1e9)
    config = BacktestConfig(resolution="MINUTE_5", trend_resolution="MINUTE_30", trend_filter="ema21_spec",
                            window=None, management=None, risk=risk, lookback=WINDOW, trend_lookback=WINDOW,
                            label="5m-v1")
    strategy = VolumePullbackStrategy(VolumePullbackConfig.parse("evp-pullback-off-rr3"))
    return [t for t in run_backtest(candles, m30, config, strategies=[strategy]).trades
            if START <= t.opened_at < END and t.role == "signal"]


def replay(m5, signals: list[Signal], ex: Exit, extra_spread: float = 0.0) -> list[dict]:
    by_time = defaultdict(list)
    for s in signals:
        by_time[s.opened_at].append(s)
    trades, pos = [], None
    day, count = None, 0
    for b in m5:
        t = b.timestamp
        half = b.spread / 2
        if t.date() != day:
            day, count = t.date(), 0
        # entries fill at this candle's open (already priced into the signal)
        if pos is None and t.weekday() != 5:
            for s in by_time.get(t, []):
                if count >= 6 or (t.weekday() == 4 and (t.hour, t.minute) >= (20, 45)):
                    break
                risk = abs(s.entry - s.stop)
                entry = s.entry + s.direction * extra_spread * b.spread
                pos = {"s": s, "entry": entry, "risk": risk, "stop": s.stop, "first": None,
                       "best": entry, "opened": t}
                count += 1
                break
        if pos is None:
            continue
        s, d, e, risk = pos["s"], pos["s"].direction, pos["entry"], pos["risk"]
        lo, hi = (b.low - half, b.high - half) if d == 1 else (b.low + half, b.high + half)
        fav, adverse = (hi, lo) if d == 1 else (lo, hi)
        r = lambda price: (price - e) * d / risk  # noqa: E731
        done = None
        if (adverse - pos["stop"]) * d <= 0:
            gap = b.open - half if d == 1 else b.open + half
            price = min(pos["stop"], gap) if d == 1 else max(pos["stop"], gap)
            done = (price, "stop" if pos["first"] is None else "runner_stop")
        elif ex.partial_r is None:
            if r(fav) >= ex.target_r:
                done = (e + d * ex.target_r * risk, "target")
        else:
            if pos["first"] is None and r(fav) >= ex.partial_r:
                pos["first"] = ex.partial_r
            close_exit = b.close - half if d == 1 else b.close + half
            if pos["first"] is not None and ex.trail_r is None and (t.hour, t.minute) == (20, 55):
                done = (close_exit, "end_of_day")
        if done is None and t.weekday() == 4 and (t.hour, t.minute) >= (20, 45):
            done = (b.close - half if d == 1 else b.close + half, "weekend")
        if done:
            price, reason = done
            whole = r(price)
            total = whole if pos["first"] is None else 0.5 * pos["first"] + 0.5 * whole
            trades.append({"opened": pos["opened"].isoformat(), "year": t.year, "direction": d,
                           "r": total, "reason": reason})
            pos = None
            continue
        if pos["first"] is not None:   # runner: breakeven, then trail from the best price
            pos["best"] = max(pos["best"], fav) if d == 1 else min(pos["best"], fav)
            trail = e if ex.trail_r is None else pos["best"] - d * ex.trail_r * risk
            pos["stop"] = max(pos["stop"], e, trail) if d == 1 else min(pos["stop"], e, trail)
    return trades


def summary(trades: list[dict]) -> dict:
    years = defaultdict(list)
    for t in trades:
        years[t["year"]].append(t["r"])
    reasons = defaultdict(int)
    for t in trades:
        reasons[t["reason"]] += 1
    rs = [t["r"] for t in trades]
    return {"trades": len(rs), "average_r": sum(rs) / len(rs) if rs else 0.0, "total_r": sum(rs),
            "win_rate": sum(r > 0 for r in rs) / len(rs) if rs else 0.0,
            "best_r": max(rs) if rs else 0.0,
            "years": {y: {"trades": len(v), "average_r": sum(v) / len(v)} for y, v in sorted(years.items())},
            "exits": dict(reasons)}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    if (OUT / "results.json").exists():
        raise SystemExit("Already run")
    m5, m30 = load("MINUTE_5"), load("MINUTE_30")
    stream = backtest(m5, m30, limits=False)
    signals = sorted({Signal(t.opened_at, 1 if t.direction == "BUY" else -1, t.entry, t.initial_stop)
                      for t in stream}, key=lambda s: s.opened_at)
    live = backtest(m5, m30, limits=True)
    reference = sum(t.r for t in live) / len(live)
    rows = {}
    for ex in EXITS:
        rows[ex.id] = summary(replay(m5, signals, ex))
        rows[ex.id]["extra_spread_average_r"] = summary(replay(m5, signals, ex, EXTRA_SPREAD_SHARE))["average_r"]
    base = rows["B"]
    for ex in EXITS[1:]:
        row = rows[ex.id]
        row["helps"] = all(row["years"][y]["average_r"] > base["years"][y]["average_r"] for y in base["years"])
        row["tradable"] = (row["helps"] and row["trades"] >= 300 and row["average_r"] > 0.05
                           and sum(v["average_r"] > 0 for v in row["years"].values()) >= 2
                           and row["extra_spread_average_r"] > 0)
    fidelity = {"run_backtest_trades": len(live), "run_backtest_average_r": reference,
                "replay_trades": base["trades"], "replay_average_r": base["average_r"],
                "valid": abs(reference - base["average_r"]) <= 0.02}
    sources = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (Path(__file__), PROTOCOL)}
    (OUT / "results.json").write_text(json.dumps({"created_utc": datetime.now(timezone.utc).isoformat(),
        "sources": sources, "signals": len(signals), "fidelity": fidelity, "rows": rows}, indent=2) + "\n")
    print(json.dumps(fidelity))
    for k, v in rows.items():
        print(k, json.dumps(v))


if __name__ == "__main__":
    main()
