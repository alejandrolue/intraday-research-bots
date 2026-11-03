"""Day-trading portfolio (#166 Part C): roster, edge sizing, messages and the bracket budget."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from goldbot import daytrade as dt
from goldbot.cli import _daytrade_settings, _parser, _settings

ROOT = Path(__file__).resolve().parents[1]


def test_committed_roster_matches_the_preregistered_protocol():
    roster = dt.load_roster(ROOT / "daytrade.json")
    assert roster.account == "YOUR_DEMO_ACCOUNT_ID"
    assert (roster.risk_per_trade, roster.max_account_daily_loss, roster.max_account_open_risk) == (0.01, 0.02, 0.03)
    assert {l.name: l.prior_r for l in roster.lanes} == {
        "gold-london": 0.043, "us100-orb": 0.073,
        "us100-orbmid5": 0.052, "us500-orbmid30": 0.032}
    assert all(l.flat_at for l in roster.lanes)
    assert [l.name for l in roster.lanes if l.entry_orders == "stop"] == ["gold-london"]


def test_lane_settings_follow_the_live_profiles():
    roster = dt.load_roster(ROOT / "daytrade.json")
    base = _settings(_parser().parse_args(["watch"]))
    lanes = {l.name: _daytrade_settings(base, roster, l) for l in roster.lanes}
    assert lanes["gold-london"].manage_trades and lanes["gold-london"].pyramid_at_r == 1e9
    assert lanes["gold-london"].entry_orders == "stop"
    assert lanes["us100-orb"].capital_trend_resolution == "HOUR_4" and lanes["us100-orb"].max_trades_per_day == 3
    assert lanes["us500-orbmid30"].minimum_reward_to_risk == 1.0 and lanes["us500-orbmid30"].capital_epic == "US500"
    assert all(s.risk_per_trade == pytest.approx(0.0125) for s in lanes.values())
    assert all(str(s.data_dir).endswith(f"data/daytrade/{n}") for n, s in lanes.items())


def test_roster_refuses_unapproved_markets_and_missing_daily_close(tmp_path):
    good = json.loads((ROOT / "daytrade.json").read_text())
    bad = json.loads(json.dumps(good)); bad["lanes"][0]["epic"] = "DE40"
    (tmp_path / "a.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="not allowed"):
        dt.load_roster(tmp_path / "a.json")
    bad = json.loads(json.dumps(good)); bad["lanes"][2]["flat_at"] = ""
    (tmp_path / "b.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="flat_at"):
        dt.load_roster(tmp_path / "b.json")
    bad = json.loads(json.dumps(good)); bad["max_multiplier"] = 3
    (tmp_path / "c.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="2%"):
        dt.load_roster(tmp_path / "c.json")


def test_edge_sizing_follows_the_preregistered_rule():
    roster = dt.load_roster(ROOT / "daytrade.json")
    mu0 = 0.05
    assert dt.edge([], mu0, roster) == {"n": 0, "posterior_r": 0.05, "multiplier": 1.0, "paused": False}
    assert dt.edge([-1.0] * 10, mu0, roster)["multiplier"] == 1.0          # fewer than 20 trades: full size
    same = dt.edge([0.05] * 30, mu0, roster)
    assert same["multiplier"] == 1.0                                       # live matches the shrunk prior
    great = dt.edge([0.3] * 30, mu0, roster)
    assert great["multiplier"] == 1.25                                     # capped
    weak = dt.edge([0.0] * 30, mu0, roster)
    assert weak["multiplier"] == pytest.approx(0.625)                      # μ = 2.5/80 = 0.03125 → ×0.625
    bad = dt.edge([-0.1] * 40, mu0, roster)
    assert bad["paused"] and bad["multiplier"] == 0.0                      # μ = (2.5 − 4)/90 < 0 at 40 trades
    floor = dt.edge([-0.07] * 30, mu0, roster)                             # μ = 0.4/80 = 0.005 → ×0.1 → floor
    assert floor["multiplier"] == 0.25 and not floor["paused"]


def _close(ts, direction, entry, stop, exit_price, pnl):
    return {"event": "PAPER_CLOSE", "timestamp": ts, "exit_price": exit_price, "broker_pnl": pnl,
            "position": {"direction": direction, "entry": entry, "initial_stop": stop, "stop": stop}}


def test_closed_r_and_day_summary(tmp_path):
    lane = tmp_path / "gold-london"; lane.mkdir()
    rows = [_close("2026-10-28T10:00:00+00:00", "BUY", 100, 90, 120, 140.0),
            _close("2026-10-28T14:00:00+00:00", "SELL", 100, 110, 110, -70.0),
            _close("2026-10-29T10:00:00+00:00", "BUY", 100, 90, 95, -35.0)]
    (lane / "journal.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert dt.closed_r(lane / "journal.jsonl") == [2.0, -1.0, -0.5]
    s = dt.day_summary({"gold-london": lane}, date(2026, 10, 28))
    assert s["trades"] == 2 and s["r"] == pytest.approx(1.0) and s["chf"] == pytest.approx(70.0)


def test_messages_and_clock(tmp_path):
    roster = dt.load_roster(ROOT / "daytrade.json")
    edges = {l.name: {"multiplier": 1.0, "n": 0, "paused": False} for l in roster.lanes}
    edges["us100-orb"] = {"multiplier": 0.0, "n": 41, "paused": True}
    text = dt.morning_text(roster, date(2026, 11, 3), edges, 7000.0, {"trades": 2, "r": 1.5, "chf": 105.0})
    assert "gold-london 08:00–13:00" in text and "us100-orb" in text and "(⏸ paused" in text
    assert "daily limit −2% (CHF 140)" in text and "Yesterday: 2 trades, +1.50R" in text
    assert "No trades today." in dt.evening_text(roster, date(2026, 11, 3), {"trades": 0}, 7000.0, False)
    clock = dt.MessageClock(tmp_path / "m.json")
    tue_0801 = datetime(2026, 11, 3, 7, 1, tzinfo=timezone.utc)          # 08:01 Zurich
    assert clock.due("morning", tue_0801) and not clock.due("evening", tue_0801)
    clock.mark("morning", tue_0801)
    assert not dt.MessageClock(tmp_path / "m.json").due("morning", tue_0801 + timedelta(minutes=5))
    assert not clock.due("morning", datetime(2026, 11, 7, 7, 1, tzinfo=timezone.utc))   # Saturday
