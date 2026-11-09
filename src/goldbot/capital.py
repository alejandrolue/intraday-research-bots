from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import DEMO_BASE_URL
from .models import Candle


class CapitalAPIError(RuntimeError):
    pass


class _SessionExpired(Exception):
    pass


def _error_detail(response: httpx.Response) -> str:
    """The broker's reason for a refused request (e.g. errorCode), so journals show why (#203)."""
    try:
        body = response.json()
        text = str(body.get("errorCode") or body) if isinstance(body, dict) else str(body)
    except Exception:
        text = response.text or ""
    text = " ".join(text.split())[:200]
    return f": {text}" if text else ""


# Capital.com allows about 10 requests per second per API key, shared by every bot.
MIN_REQUEST_INTERVAL = 0.15  # seconds between requests from one client (at most ~7 per second)
RATE_LIMIT_RETRIES = (1.0, 2.0)  # waits before retrying a throttled GET
MAX_RETRY_AFTER = 5.0


class CapitalDemoClient:
    """Client for Capital.com's hard-coded demo API."""

    def __init__(
        self,
        api_key: str,
        identifier: str,
        password: str,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        min_interval: float = MIN_REQUEST_INTERVAL,
        retry_waits: tuple[float, ...] = RATE_LIMIT_RETRIES,
        sleep: Any = time.sleep,
        clock: Any = time.monotonic,
        base_url: str = DEMO_BASE_URL,
    ) -> None:
        if base_url != DEMO_BASE_URL:
            raise ValueError("This client only supports Capital.com's demo API")
        self._api_key = api_key
        self._identifier = identifier
        self._password = password
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
        )
        self._cst: str | None = None
        self._security_token: str | None = None
        self._account_id: str | None = None
        self._min_interval = min_interval
        self._retry_waits = retry_waits
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None

    def __enter__(self) -> "CapitalDemoClient":
        self.login()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def login(self) -> None:
        for attempt in range(4):
            try:
                response = self._request(
                    "POST",
                    "/session",
                    headers={"X-CAP-API-KEY": self._api_key},
                    json={
                        "identifier": self._identifier,
                        "password": self._password,
                        "encryptedPassword": False,
                    },
                    authenticated=False,
                )
                break
            except CapitalAPIError as exc:
                # Capital.com allows about one login per second per API key.
                if "HTTP 429" not in str(exc) or attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
        self._cst = response.headers.get("CST")
        self._security_token = response.headers.get("X-SECURITY-TOKEN")
        if not self._cst or not self._security_token:
            raise CapitalAPIError("Capital.com login succeeded without returning session tokens")
        if self._account_id is not None:
            self._activate(self._account_id)

    def get_accounts(self) -> list[dict[str, Any]]:
        accounts = self._request("GET", "/accounts").json().get("accounts")
        if not isinstance(accounts, list):
            raise CapitalAPIError("Capital.com accounts response did not contain an accounts list")
        return accounts

    def use_account(self, name_or_id: str) -> dict[str, Any]:
        """Make a demo sub-account active for this session, matched by ID or name."""
        accounts = self.get_accounts()
        wanted = name_or_id.strip().lower()
        matches = [
            account
            for account in accounts
            if str(account.get("accountId")) == name_or_id
            or str(account.get("accountName") or "").strip().lower() == wanted
        ]
        if len(matches) != 1:
            names = ", ".join(repr(account.get("accountName")) for account in accounts)
            raise CapitalAPIError(f"No single Capital.com account matches {name_or_id!r} ({names})")
        account = matches[0]
        self._account_id = str(account["accountId"])
        self._activate(self._account_id)
        return account

    def _activate(self, account_id: str) -> None:
        current = self._request("GET", "/session").json().get("accountId")
        if str(current) != account_id:
            self._request("PUT", "/session", json={"accountId": account_id})

    def search_markets(self, term: str) -> list[dict[str, Any]]:
        response = self._request("GET", "/markets", params={"searchTerm": term})
        payload = response.json()
        markets = payload.get("markets")
        if not isinstance(markets, list):
            raise CapitalAPIError("Capital.com market response did not contain a markets list")
        return markets

    def get_market_details(self, epic: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/markets/{epic}").json())

    def get_preferences(self) -> dict[str, Any]:
        return dict(self._request("GET", "/accounts/preferences").json())

    def get_positions(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/positions").json()
        positions = payload.get("positions")
        if not isinstance(positions, list):
            raise CapitalAPIError("Capital.com positions response did not contain a positions list")
        return positions

    def create_position(
        self,
        *,
        epic: str,
        direction: str,
        size: float,
        stop_level: float,
        profit_level: float,
    ) -> str:
        if direction not in {"BUY", "SELL"}:
            raise ValueError("direction must be BUY or SELL")
        response = self._request(
            "POST",
            "/positions",
            json={
                "epic": epic,
                "direction": direction,
                "size": size,
                "guaranteedStop": False,
                "stopLevel": stop_level,
                "profitLevel": profit_level,
            },
        )
        deal_reference = response.json().get("dealReference")
        if not deal_reference:
            raise CapitalAPIError("Capital.com create-position response omitted dealReference")
        return str(deal_reference)

    def create_working_order(
        self,
        *,
        epic: str,
        direction: str,
        size: float,
        level: float,
        stop_level: float,
        profit_level: float,
        good_till: datetime,
        order_type: str = "STOP",
    ) -> str:
        """A pending STOP/LIMIT entry order (issue #101). Never retried automatically."""
        if direction not in {"BUY", "SELL"} or order_type not in {"STOP", "LIMIT"}:
            raise ValueError("direction must be BUY/SELL and order_type STOP/LIMIT")
        response = self._request(
            "POST",
            "/workingorders",
            json={
                "epic": epic,
                "direction": direction,
                "size": size,
                "level": level,
                "type": order_type,
                "goodTillDate": good_till.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                "guaranteedStop": False,
                "stopLevel": stop_level,
                "profitLevel": profit_level,
            },
        )
        deal_reference = response.json().get("dealReference")
        if not deal_reference:
            raise CapitalAPIError("Capital.com working-order response omitted dealReference")
        return str(deal_reference)

    def get_working_orders(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/workingorders").json()
        orders = payload.get("workingOrders")
        if not isinstance(orders, list):
            raise CapitalAPIError("Capital.com working-orders response did not contain a list")
        return orders

    def delete_working_order(self, deal_id: str) -> str:
        response = self._request("DELETE", f"/workingorders/{deal_id}")
        return str(response.json().get("dealReference") or "")

    def get_activity(self, *, last_period: int = 86_400) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            "/history/activity",
            params={"lastPeriod": last_period, "detailed": "true"},
        ).json()
        activities = payload.get("activities")
        if not isinstance(activities, list):
            raise CapitalAPIError("Capital.com activity response did not contain an activities list")
        return activities

    def get_transactions(
        self,
        *,
        last_period: int = 86_400,
        transaction_type: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"lastPeriod": last_period}
        if transaction_type:
            params["type"] = transaction_type
        payload = self._request("GET", "/history/transactions", params=params).json()
        transactions = payload.get("transactions")
        if not isinstance(transactions, list):
            raise CapitalAPIError(
                "Capital.com transaction response did not contain a transactions list"
            )
        return transactions

    def close_position(self, deal_id: str) -> str:
        response = self._request("DELETE", f"/positions/{deal_id}")
        deal_reference = response.json().get("dealReference")
        if not deal_reference:
            raise CapitalAPIError("Capital.com close-position response omitted dealReference")
        return str(deal_reference)

    def update_position(self, deal_id: str, *, stop_level: float, profit_level: float) -> str:
        response = self._request(
            "PUT",
            f"/positions/{deal_id}",
            json={"stopLevel": stop_level, "profitLevel": profit_level},
        )
        deal_reference = response.json().get("dealReference")
        if not deal_reference:
            raise CapitalAPIError("Capital.com update-position response omitted dealReference")
        return str(deal_reference)

    def get_confirmation(self, deal_reference: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/confirms/{deal_reference}").json())

    def get_prices(
        self,
        epic: str,
        *,
        resolution: str = "MINUTE_5",
        max_count: int = 200,
    ) -> tuple[list[Candle], dict[str, Any]]:
        if not 1 <= max_count <= 1000:
            raise ValueError("max_count must be between 1 and 1000")
        response = self._request(
            "GET",
            f"/prices/{epic}",
            params={"resolution": resolution, "max": max_count},
        )
        payload = response.json()
        raw_prices = payload.get("prices")
        if not isinstance(raw_prices, list):
            raise CapitalAPIError("Capital.com price response did not contain a prices list")
        candles = [Candle.from_capital(item) for item in raw_prices]
        candles.sort(key=lambda candle: candle.timestamp)
        metadata = {"instrumentType": payload.get("instrumentType")}
        return candles, metadata

    def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            return self._send(method, path, authenticated=authenticated, **kwargs)
        except _SessionExpired:
            # Capital.com sessions expire after inactivity; log in again once and retry.
            self.login()
            try:
                return self._send(method, path, authenticated=authenticated, **kwargs)
            except _SessionExpired as exc:
                raise CapitalAPIError(
                    f"Capital.com API returned HTTP 401 for {method} {path}"
                ) from exc

    def _send(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool,
        **kwargs: Any,
    ) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        if authenticated:
            if not self._cst or not self._security_token:
                raise CapitalAPIError("Capital.com client is not authenticated")
            headers.update({"CST": self._cst, "X-SECURITY-TOKEN": self._security_token})
        try:
            response = self._paced_request(method, path, headers=headers, **kwargs)
            # A throttled read is safe to repeat; orders and closes are not retried here.
            for wait in self._retry_waits if method == "GET" else ():
                if response.status_code != 429:
                    break
                self._sleep(self._retry_after(response, wait))
                response = self._paced_request(method, path, headers=headers, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            if authenticated and exc.response.status_code == 401:
                raise _SessionExpired from exc
            raise CapitalAPIError(
                f"Capital.com API returned HTTP {exc.response.status_code} for {method} {path}"
                f"{_error_detail(exc.response) if path != '/session' else ''}"
            ) from exc
        except httpx.HTTPError as exc:
            raise CapitalAPIError(f"Capital.com API request failed for {method} {path}") from exc

    def _paced_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Keep a minimum gap between requests so bursts stay under the broker's limit."""
        if self._last_request is not None:
            wait = self._last_request + self._min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        try:
            return self._client.request(method, path, **kwargs)
        finally:
            self._last_request = self._clock()

    @staticmethod
    def _retry_after(response: httpx.Response, default: float) -> float:
        try:
            seconds = float(response.headers.get("Retry-After", default))
        except ValueError:
            seconds = default
        return min(max(seconds, 0.0), MAX_RETRY_AFTER)
