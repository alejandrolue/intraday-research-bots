import json
from dataclasses import asdict

from goldbot.cli import _record_price_path
from goldbot.paper import PaperPosition
from goldbot.pricepath import PricePathRecorder
from goldbot.telegram import TelegramStatusNotifier


def _buy() -> PaperPosition:
    return PaperPosition(
        direction="BUY", entry=100.0, stop=98.0, target=104.0, units=1.0,
        opened_at="2026-09-29T19:00:00+00:00", strategy="ema_pullback", deal_id="d1",
        local_id="a1",
    )


def _close(position: PaperPosition) -> dict:
    return {"event": "PAPER_CLOSE", "timestamp": "2026-09-29T20:00:00+00:00",
            "exit_reason": "TARGET", "exit_price": 104.0, "position": asdict(position)}


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_records_extremes_on_the_closing_side_of_the_quote(tmp_path):
    recorder = PricePathRecorder(tmp_path / "paths.jsonl")
    recorder.mark_started()
    position = _buy()
    for bid in (99.0, 102.0, 101.0):
        recorder.record([position], bid=bid, offer=bid + 0.5, timestamp=f"t{bid}")
    summary = recorder.finish(_close(position))
    assert summary["mfe_r"] == 1.0 and summary["mfe_at"] == "t102.0"
    assert summary["mae_r"] == -0.5 and summary["samples"] == 3
    assert summary["partial"] is False
    rows = _rows(tmp_path / "paths.jsonl")
    assert [row["event"] for row in rows] == ["PATH_SAMPLE"] * 3 + ["PATH_CLOSE"]
    assert rows[-1]["exit_reason"] == "TARGET" and rows[-1]["mfe_r"] == 1.0
    assert recorder.finish(_close(position)) is None


def test_sell_uses_the_offer_and_positions_open_at_start_are_partial(tmp_path):
    recorder = PricePathRecorder(tmp_path / "paths.jsonl")
    sell = PaperPosition(direction="SELL", entry=100.0, stop=102.0, target=96.0, units=1.0,
                         opened_at="x", local_id="s1", deal_id="d2")
    recorder.record([sell], bid=97.5, offer=98.0, timestamp="t1")
    summary = recorder.finish(_close(sell))
    assert summary["mfe_r"] == 1.0 and summary["partial"] is True


def test_resumes_after_a_restart(tmp_path):
    path = tmp_path / "paths.jsonl"
    position = _buy()
    first = PricePathRecorder(path)
    first.mark_started()
    first.record([position], bid=97.0, offer=97.5, timestamp="t1")
    second = PricePathRecorder(path)
    second.record([position], bid=103.0, offer=103.5, timestamp="t2")
    summary = second.finish(_close(position))
    assert summary["mae_r"] == -1.5 and summary["mfe_r"] == 1.5
    assert summary["samples"] == 2 and summary["partial"] is False


class _Client:
    def __init__(self):
        self.calls = 0

    def get_market_details(self, epic):
        self.calls += 1
        return {"snapshot": {"marketStatus": "TRADEABLE", "bid": 101.0, "offer": 101.3}}


class _Ledger:
    def __init__(self, positions):
        self.open_positions = positions


def test_record_price_path_makes_no_call_while_flat(tmp_path):
    client, recorder = _Client(), PricePathRecorder(tmp_path / "paths.jsonl")
    _record_price_path(client, "GOLD", _Ledger([]), recorder)
    assert client.calls == 0
    _record_price_path(client, "GOLD", _Ledger([_buy()]), recorder)
    assert client.calls == 1
    assert _rows(tmp_path / "paths.jsonl")[0]["r"] == 0.5


def test_close_message_shows_the_path():
    sent = []
    notifier = TelegramStatusNotifier.__new__(TelegramStatusNotifier)
    notifier.market = "GOLD"
    notifier._send = sent.append
    event = _close(_buy())
    event.update(pnl=4.0, balance=1004.0,
                 path={"mfe_r": 2.1, "mae_r": -0.4, "partial": False})
    notifier.trade_closed(event)
    assert "Path: best +2.10R, worst -0.40R" in sent[0]
