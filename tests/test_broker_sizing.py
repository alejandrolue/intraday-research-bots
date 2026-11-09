"""Issue #198: demo bots size from their account's broker balance."""
from datetime import datetime, timedelta, timezone

from goldbot.lab import BrokerBalance
from goldbot.paper import PaperLedger

NOW = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)


def _ledger(tmp_path, balance=1000.0):
    return PaperLedger(tmp_path / "state.json", tmp_path / "journal.jsonl",
                       starting_balance=balance, point_value_per_unit=1.0)


class Accounts:
    def __init__(self, balances):
        self.balances = list(balances)
        self.calls = 0

    def get_accounts(self):
        self.calls += 1
        value = self.balances.pop(0)
        if isinstance(value, Exception):
            raise value
        return [{"accountId": "A1", "balance": {"balance": value}}, {"accountId": "B", "balance": {"balance": 5}}]


def test_risk_state_uses_the_broker_balance_when_set(tmp_path):
    ledger = _ledger(tmp_path, 1000.0)
    assert ledger.risk_state(NOW).balance == 1000.0
    ledger.sizing_balance = 11000.0
    assert ledger.risk_state(NOW).balance == 11000.0
    assert ledger.state.balance == 1000.0  # the ledger still tracks its own P&L


def test_broker_balance_is_cached_and_applied(tmp_path):
    client = Accounts([11000.0, 12000.0])
    broker = BrokerBalance(client, "A1", ttl_seconds=300)
    ledgers = [_ledger(tmp_path / "a"), _ledger(tmp_path / "b")]
    for d in ("a", "b"):
        (tmp_path / d).mkdir(exist_ok=True)
    assert broker.apply(ledgers, NOW) == 11000.0
    assert all(l.sizing_balance == 11000.0 for l in ledgers)
    assert broker.apply(ledgers, NOW + timedelta(seconds=60)) == 11000.0
    assert client.calls == 1
    assert broker.apply(ledgers, NOW + timedelta(seconds=301)) == 12000.0
    assert client.calls == 2


def test_failed_read_keeps_last_value_and_none_before_first_read(tmp_path):
    client = Accounts([RuntimeError("429"), 11000.0, RuntimeError("down")])
    broker = BrokerBalance(client, "A1", ttl_seconds=300)
    ledger = _ledger(tmp_path)
    assert broker.apply([ledger], NOW) is None
    assert ledger.sizing_balance is None
    assert ledger.risk_state(NOW).balance == 1000.0  # falls back to the ledger
    assert broker.apply([ledger], NOW + timedelta(seconds=301)) == 11000.0
    assert broker.apply([ledger], NOW + timedelta(seconds=602)) == 11000.0
    assert broker.last_error == "down"
