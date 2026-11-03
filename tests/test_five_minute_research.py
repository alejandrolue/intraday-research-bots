from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
from five_minute_strategies import SessionStrategy, Variant, variants
from five_minute_study import eligible, select
from goldbot.models import Candle


def candles(local_day="2025-01-06", session="london"):
    strategy = SessionStrategy(Variant("orb", session, 30, 2.0, None))
    opened = datetime.fromisoformat(local_day).replace(
        hour=8, minute=0 if session == "london" else 30, tzinfo=strategy.zone
    ).astimezone(timezone.utc)
    return [Candle(opened + timedelta(minutes=5 * i), 100, 101, 99, 100, spread=0.3)
            for i in range(-20, 13)]


@pytest.mark.parametrize("family", ["orb", "rolling_sweep"])
def test_triggers_are_symmetric_and_have_executable_levels(family):
    data = candles()
    time = data[-1].timestamp
    if family == "orb":
        data[-1] = Candle(time, 100, 102, 99.5, 101.8, spread=0.3)
    else:
        data[-1] = Candle(time, 100, 100.8, 98, 100.3, spread=0.3)
    variant = Variant(family, "london", 30, 2.0, None)
    long = SessionStrategy(variant).analyze(data)
    mirrored = [Candle(c.timestamp, 200-c.open, 200-c.low, 200-c.high, 200-c.close,
                       spread=c.spread) for c in data]
    short = SessionStrategy(variant).analyze(mirrored)
    assert long.decision == "READY_LONG"
    assert short.decision == "READY_SHORT"
    assert long.stop < long.entry < long.target
    assert short.target < short.entry < short.stop
    assert short.stop == pytest.approx(200 - long.stop)
    assert short.target == pytest.approx(200 - long.target)


@pytest.mark.parametrize("day", ["2025-03-14", "2025-03-31", "2025-10-27", "2025-11-03"])
@pytest.mark.parametrize("session", ["london", "ny"])
def test_session_range_follows_local_time_across_dst(day, session):
    data = candles(day, session)
    bar = data[-1]
    data[-1] = Candle(bar.timestamp, 100, 102, 99.5, 101.8, spread=0.3)
    strategy = SessionStrategy(Variant("orb", session, 30, 2.0, None))
    assert strategy.analyze(data).ready
    # Missing even one opening-range bar must suppress the signal.
    assert not strategy.analyze(data[:21] + data[22:]).ready


def test_ambiguous_sweeps_and_pre_start_signals_are_rejected():
    data = candles()
    bar = data[-1]
    data[-1] = Candle(bar.timestamp, 100, 102, 98, 100.3, spread=0.3)
    variant = Variant("rolling_sweep", "london", 30, 2.0, None)
    assert not SessionStrategy(variant).analyze(data).ready
    data[-1] = Candle(bar.timestamp, 100, 100.8, 98, 100.3, spread=0.3)
    assert not SessionStrategy(variant, start=bar.timestamp + timedelta(minutes=5)).analyze(data).ready


def test_grid_and_selection_do_not_promote_an_ineligible_high_return():
    assert len(variants()) == len({v.id for v in variants()}) == 32
    rows = []
    for family in ("orb", "rolling_sweep"):
        for reward, avg, count in ((1.6, 0.1, 160), (2.0, 1.0, 20)):
            variant = Variant(family, "london", 30, reward, None)
            rows.append({"variant": variant.__dict__, "label": variant.id,
                         "average_r": avg, "max_drawdown_r": 10, "trades": count,
                         "quarters": [1, 2, 3, -1],
                         "side": {"BUY": {"total_r": 2}, "SELL": {"total_r": 2}}})
    assert all(s["eligible"] and s["variant"]["reward"] == 1.6 for s in select(rows))
    for row in rows:
        row["side"]["SELL"]["total_r"] = -1
    assert not any(eligible(row) for row in rows)
    assert not any(s["eligible"] for s in select(rows))


def test_crossed_quote_spreads_are_repaired_from_the_previous_candle(tmp_path, monkeypatch) -> None:
    import five_minute_study as study
    from goldbot.history import _write
    from goldbot.models import Candle
    from datetime import datetime, timedelta, timezone

    start = datetime(2025, 2, 7, 16, 5, tzinfo=timezone.utc)
    bars = [Candle(start + timedelta(minutes=5 * i), 100, 101, 99, 100, spread=s)
            for i, s in enumerate((0.3, 0.4, -0.96, 0.3))]
    _write(tmp_path / "MINUTE_5" / "2025-02.csv", bars)
    monkeypatch.setattr(study, "HISTORY", tmp_path)

    loaded = study.load("MINUTE_5", datetime(2025, 3, 1, tzinfo=timezone.utc))

    assert [bar.spread for bar in loaded] == [0.3, 0.4, 0.4, 0.3]
    assert study.REPAIRED["MINUTE_5"] == [bars[2].timestamp.isoformat()]
