from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from goldbot.backtest import BacktestConfig, run_backtest
from goldbot.management import ManagementConfig
from goldbot.models import Candle, MarketSetup
from goldbot.risk import RiskConfig


START = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)  # inside London/NY hours


def bar(index: int, open_: float, high: float, low: float, close: float, spread: float = 0.2) -> Candle:
    return Candle(START + timedelta(minutes=5 * index), open_, high, low, close, spread=spread)


class Scripted:
    """Signals READY_LONG at the close of one bar: entry=close, stop 2 below, target 4 above."""

    name = "scripted"
    minimum_candles = 1

    def __init__(self, signal_index: int) -> None:
        self.signal_time = START + timedelta(minutes=5 * signal_index)

    def analyze(self, candles: list[Candle]) -> MarketSetup:
        last = candles[-1]
        if last.timestamp != self.signal_time:
            return MarketSetup("NO_TRADE", last.timestamp, "none", "NEUTRAL", strategy=self.name)
        return MarketSetup(
            "READY_LONG", last.timestamp, "go", "BULLISH", strategy=self.name,
            entry=last.close, stop=last.close - 2, target=last.close + 4,
        )


def config(**changes: object) -> BacktestConfig:
    base = BacktestConfig(
        trend_resolution=None,
        window=None,
        management=None,
        risk=RiskConfig(max_trades_per_day=15, max_positions=3, max_spread=1.0),
        lookback=2,
        units=1.0,
    )
    from dataclasses import replace

    return replace(base, **changes)  # type: ignore[arg-type]


def flat(count: int, price: float = 100.0) -> list[Candle]:
    return [bar(i, price, price + 0.3, price - 0.3, price) for i in range(count)]


def test_fills_at_next_open_ask_and_hits_target_at_bid() -> None:
    candles = flat(3) + [bar(3, 100.1, 104.5, 100.0, 104.3), bar(4, 104.3, 104.4, 104.2, 104.3)]

    result = run_backtest(candles, None, config(), strategies=[Scripted(2)])

    [trade] = result.trades
    assert round(trade.entry, 2) == 100.2  # open 100.1 + half spread 0.1
    assert trade.exit_reason == "TARGET"
    # Stop/target shifted by the 0.2 entry drift: target 104.2.
    assert round(trade.exit, 2) == 104.2
    assert round(trade.r, 2) == 2.0


def test_stop_is_assumed_first_when_a_bar_touches_both() -> None:
    candles = flat(3) + [bar(3, 100.0, 105.0, 97.0, 101.0)]

    [trade] = run_backtest(candles, None, config(), strategies=[Scripted(2)]).trades

    assert trade.exit_reason == "STOP"
    assert round(trade.r, 2) == -1.0


def test_gap_through_stop_fills_at_the_open() -> None:
    candles = flat(3) + [bar(3, 100.0, 100.2, 99.9, 100.0), bar(4, 96.0, 96.5, 95.5, 96.0)]

    [trade] = run_backtest(candles, None, config(), strategies=[Scripted(2)]).trades

    assert round(trade.exit, 2) == 95.9  # open 96.0 at the bid
    assert trade.r < -2


def test_entries_that_drift_too_far_are_skipped() -> None:
    candles = flat(3) + [bar(3, 101.5, 101.6, 101.4, 101.5)]

    result = run_backtest(candles, None, config(), strategies=[Scripted(2)])

    assert result.trades == []
    assert result.skipped["entry_drift"] == 1


def test_management_trails_into_profit_and_adds_to_the_winner() -> None:
    candles = flat(3) + [
        bar(3, 100.0, 102.6, 99.9, 102.5),  # +1.1R at the close: stop to breakeven, add queued
        bar(4, 102.5, 104.0, 102.4, 103.9),  # add fills at 102.6; stop trails up
        bar(5, 103.9, 104.0, 101.0, 101.2),  # pullback hits the trailed stops
    ]

    result = run_backtest(
        candles, None, config(management=ManagementConfig()), strategies=[Scripted(2)]
    )

    roles = sorted(trade.role for trade in result.trades)
    assert roles == ["add", "signal"]
    signal = next(trade for trade in result.trades if trade.role == "signal")
    assert signal.exit_reason == "TRAILING_STOP"
    assert signal.r > 0


def test_trading_window_blocks_signals_outside_hours() -> None:
    from goldbot.sessions import TradingWindow

    night = datetime(2026, 7, 1, 1, 0, tzinfo=timezone.utc)
    candles = [
        Candle(night + timedelta(minutes=5 * i), 100, 100.3, 99.7, 100, spread=0.2) for i in range(5)
    ]
    scripted = Scripted(0)
    scripted.signal_time = candles[2].timestamp

    result = run_backtest(candles, None, config(window=TradingWindow()), strategies=[scripted])

    assert result.trades == []


def test_long_only_skips_short_signals() -> None:
    class Short(Scripted):
        def analyze(self, candles: list[Candle]) -> MarketSetup:
            setup = super().analyze(candles)
            if not setup.ready:
                return setup
            last = candles[-1]
            return MarketSetup(
                "READY_SHORT", last.timestamp, "go", "BEARISH", strategy=self.name,
                entry=last.close, stop=last.close + 2, target=last.close - 4,
            )

    candles = flat(3) + [bar(3, 100.0, 100.2, 99.8, 100.0)]

    result = run_backtest(candles, None, config(directions="long"), strategies=[Short(2)])

    assert result.trades == []
    assert result.skipped["direction"] == 1


def test_adx_filter_skips_signals_in_sideways_markets() -> None:
    candles = flat(40) + [bar(40, 100.0, 100.2, 99.8, 100.0)]

    result = run_backtest(
        candles, None, config(min_adx=20.0, lookback=35), strategies=[Scripted(38)]
    )

    assert result.trades == []
    assert result.skipped["adx_below_minimum"] == 1


def test_history_download_treats_empty_holiday_ranges_as_no_candles(tmp_path) -> None:
    from goldbot.capital import CapitalAPIError
    from goldbot.history import _fetch_range

    class Client:
        calls = 0

        def _request(self, method: str, path: str, **kwargs: object):  # type: ignore[no-untyped-def]
            Client.calls += 1
            raise CapitalAPIError("Capital.com API returned HTTP 404 for GET /prices/HK50")

    start = datetime(2026, 2, 16, tzinfo=timezone.utc)
    assert _fetch_range(Client(), "HK50", "MINUTE_5", start, start + timedelta(days=1)) == []  # type: ignore[arg-type]

    class Broken(Client):
        def _request(self, method: str, path: str, **kwargs: object):  # type: ignore[no-untyped-def]
            raise CapitalAPIError("Capital.com API returned HTTP 500 for GET /prices/HK50")

    import pytest
    with pytest.raises(CapitalAPIError):
        _fetch_range(Broken(), "HK50", "MINUTE_5", start, start + timedelta(days=1))  # type: ignore[arg-type]


def test_history_refetches_a_month_cached_before_it_ended(tmp_path, monkeypatch) -> None:
    import os
    from datetime import datetime, timezone

    import goldbot.history as history
    from goldbot.models import Candle

    calls = []

    def fake_fetch(client, epic, resolution, start, end):
        calls.append(start)
        return [Candle(start, 1.0, 1.0, 1.0, 1.0)]

    monkeypatch.setattr(history, "_fetch_range", fake_fetch)
    month = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    history.load_history(None, "GOLD", "MINUTE_5", month, end, tmp_path)
    path = tmp_path / "GOLD" / "MINUTE_5" / "2026-08.csv"
    stale = datetime(2026, 8, 20, tzinfo=timezone.utc).timestamp()
    os.utime(path, (stale, stale))
    history.load_history(None, "GOLD", "MINUTE_5", month, end, tmp_path)
    assert len(calls) == 2  # saved mid-month, so fetched again
    history.load_history(None, "GOLD", "MINUTE_5", month, end, tmp_path)
    assert len(calls) == 2  # now saved after the month ended: read from the cache


def test_history_repairs_a_candle_whose_mid_high_is_too_low() -> None:
    from goldbot.history import _repaired

    item = {"snapshotTimeUTC": "2015-03-02T00:00:00",
            "openPrice": {"bid": 50.0, "ask": 50.1}, "highPrice": {"bid": 49.0, "ask": 49.1},
            "lowPrice": {"bid": 48.0, "ask": 48.1}, "closePrice": {"bid": 51.0, "ask": 51.2}}
    candle = _repaired(item)
    assert candle is not None
    assert candle.high == 51.1 and candle.low == 48.05 and candle.spread == pytest.approx(0.2)


def test_flat_at_closes_open_positions_and_blocks_late_entries() -> None:
    # A long opened in the morning is closed at the first candle ending at 15:45 New York.
    from goldbot.models import MarketSetup as _Setup

    class OneLong:
        name = "one_long"
        minimum_candles = 2

        def __init__(self) -> None:
            self.done = False

        def analyze(self, candles):
            bar = candles[-1]
            if not self.done and bar.timestamp.hour == 14 and bar.timestamp.minute == 0:
                self.done = True
                return _Setup("READY_LONG", bar.timestamp, "t", "BULLISH", self.name,
                              entry=bar.close, stop=bar.close - 50, target=bar.close + 100)
            if bar.timestamp.hour == 20 and bar.timestamp.minute == 0:
                return _Setup("READY_LONG", bar.timestamp, "late", "BULLISH", self.name,
                              entry=bar.close, stop=bar.close - 50, target=bar.close + 100)
            return _Setup("NO_TRADE", bar.timestamp, "none", "NEUTRAL", self.name)

    start = datetime(2026, 7, 1, 13, 0, tzinfo=timezone.utc)
    candles = [Candle(start + timedelta(minutes=5 * i), 100.0, 100.5, 99.5, 100.0, spread=0.0)
               for i in range(120)]
    config = BacktestConfig(resolution="MINUTE_5", trend_resolution=None, window=None, management=None,
                            risk=RiskConfig(max_positions=1, max_trades_per_day=5), lookback=2,
                            flat_at=("America/New_York", 15, 45))
    result = run_backtest(candles, None, config, strategies=[OneLong()])
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "END_OF_DAY"
    assert trade.closed_at == datetime(2026, 7, 1, 19, 40, tzinfo=timezone.utc)  # 15:40-15:45 NY candle
    assert result.skipped["after_day_close"] >= 1
