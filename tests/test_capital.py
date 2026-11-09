from __future__ import annotations

import json

import httpx
import pytest

from goldbot.capital import CapitalAPIError, CapitalDemoClient
from goldbot.config import DEMO_BASE_URL


def test_demo_client_authenticates_and_parses_prices() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/session"):
            assert request.headers["X-CAP-API-KEY"] == "demo-key"
            body = json.loads(request.content)
            assert body == {
                "identifier": "person@example.com",
                "password": "local-secret",
                "encryptedPassword": False,
            }
            return httpx.Response(
                200,
                headers={"CST": "cst-token", "X-SECURITY-TOKEN": "security-token"},
                json={"accountType": "CFD"},
            )
        assert request.headers["CST"] == "cst-token"
        assert request.headers["X-SECURITY-TOKEN"] == "security-token"
        return httpx.Response(
            200,
            json={
                "prices": [
                    {
                        "snapshotTimeUTC": "2026-09-28T20:00:00",
                        "openPrice": {"bid": 4110.0, "ask": 4110.5},
                        "highPrice": {"bid": 4112.0, "ask": 4112.5},
                        "lowPrice": {"bid": 4108.0, "ask": 4108.5},
                        "closePrice": {"bid": 4111.0, "ask": 4111.5},
                        "lastTradedVolume": 123,
                    }
                ]
            },
        )

    client = CapitalDemoClient(
        "demo-key",
        "person@example.com",
        "local-secret",
        transport=httpx.MockTransport(handler),
    )
    client.login()
    candles, _ = client.get_prices("GOLD", max_count=100)
    client.close()

    assert str(requests[0].url).startswith(DEMO_BASE_URL)
    assert candles[0].close == 4111.25
    assert candles[0].spread == 0.5
    assert candles[0].volume == 123


def test_demo_client_submits_and_confirms_protected_position() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/session"):
            return httpx.Response(
                200,
                headers={"CST": "cst-token", "X-SECURITY-TOKEN": "security-token"},
                json={},
            )
        if request.method == "POST" and request.url.path.endswith("/positions"):
            assert json.loads(request.content) == {
                "epic": "GOLD",
                "direction": "BUY",
                "size": 1.0,
                "guaranteedStop": False,
                "stopLevel": 4090.0,
                "profitLevel": 4140.0,
            }
            return httpx.Response(200, json={"dealReference": "o_demo"})
        if request.url.path.endswith("/confirms/o_demo"):
            return httpx.Response(
                200,
                json={
                    "dealStatus": "ACCEPTED",
                    "dealId": "demo-deal",
                    "level": 4100.0,
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = CapitalDemoClient(
        "demo-key",
        "person@example.com",
        "local-secret",
        transport=httpx.MockTransport(handler),
    )
    client.login()
    reference = client.create_position(
        epic="GOLD",
        direction="BUY",
        size=1.0,
        stop_level=4090.0,
        profit_level=4140.0,
    )
    confirmation = client.get_confirmation(reference)
    client.close()

    assert reference == "o_demo"
    assert confirmation["dealStatus"] == "ACCEPTED"
    assert all(str(request.url).startswith(DEMO_BASE_URL) for request in requests)


def test_demo_client_reads_account_transactions() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/session"):
            return httpx.Response(
                200,
                headers={"CST": "cst-token", "X-SECURITY-TOKEN": "security-token"},
                json={},
            )
        assert request.url.path.endswith("/history/transactions")
        assert request.url.params["lastPeriod"] == "86400"
        assert request.url.params["type"] == "TRADE"
        return httpx.Response(
            200,
            json={
                "transactions": [
                    {
                        "dealId": "position-1",
                        "transactionType": "TRADE",
                        "size": "-0.19",
                        "currency": "CHF",
                    }
                ]
            },
        )

    client = CapitalDemoClient(
        "demo-key",
        "person@example.com",
        "local-secret",
        transport=httpx.MockTransport(handler),
    )
    client.login()
    transactions = client.get_transactions(transaction_type="TRADE")
    client.close()

    assert transactions[0]["size"] == "-0.19"
    assert len(requests) == 2


def _throttle_client(handler, sleeps, now):
    return CapitalDemoClient(
        "demo-key", "person@example.com", "local-secret",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append, clock=lambda: now[0],
    )


def _session(request: httpx.Request) -> httpx.Response | None:
    if request.url.path.endswith("/session"):
        return httpx.Response(200, headers={"CST": "c", "X-SECURITY-TOKEN": "s"}, json={})
    return None


def test_demo_client_retries_a_throttled_read_then_succeeds() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if (response := _session(request)) is not None:
            return response
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "30"})
        return httpx.Response(200, json={"accounts": [{"accountId": "a"}]})

    sleeps: list[float] = []
    client = _throttle_client(handler, sleeps, [100.0])
    client.login()
    assert client.get_accounts() == [{"accountId": "a"}]
    assert len(calls) == 2
    assert 5.0 in sleeps  # Retry-After honoured but capped


def test_demo_client_gives_up_after_the_retries_and_never_retries_orders() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if (response := _session(request)) is not None:
            return response
        calls.append(request.method)
        return httpx.Response(429)

    sleeps: list[float] = []
    client = _throttle_client(handler, sleeps, [100.0])
    client.login()
    try:
        client.get_accounts()
    except Exception as exc:  # noqa: BLE001
        assert "HTTP 429" in str(exc)
    assert calls == ["GET", "GET", "GET"]
    calls.clear()
    try:
        client.close_position("deal-1")
    except Exception as exc:  # noqa: BLE001
        assert "HTTP 429" in str(exc)
    assert calls == ["DELETE"]


def test_demo_client_spaces_requests_apart() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _session(request) or httpx.Response(200, json={"accounts": []})

    sleeps: list[float] = []
    now = [100.0]
    client = _throttle_client(handler, sleeps, now)
    client.login()
    client.get_accounts()
    now[0] += 0.05
    client.get_accounts()
    assert sleeps == pytest.approx([0.15, 0.10])


def test_a_refused_request_carries_the_broker_error_code() -> None:
    # 6 Nov 2026: a stop order was refused with a bare "HTTP 400"; the reason is now journaled (#203)
    def handler(request: httpx.Request) -> httpx.Response:
        return _session(request) or httpx.Response(400, json={"errorCode": "error.invalid.stoploss.minvalue: 4041.4"})

    client = _throttle_client(handler, [], [0.0])
    client.login()
    try:
        client.get_market_details("GOLD")
    except CapitalAPIError as exc:
        assert str(exc) == ("Capital.com API returned HTTP 400 for GET /markets/GOLD: "
                            "error.invalid.stoploss.minvalue: 4041.4")
    else:
        raise AssertionError("expected CapitalAPIError")
