"""Download and cache historical candles from Capital.com for backtesting."""

from __future__ import annotations

import csv
import os
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .capital import CapitalAPIError, CapitalDemoClient
from .models import Candle, _midpoint, _parse_timestamp
from .sessions import resolution_delta


FIELDS = ("timestamp", "open", "high", "low", "close", "volume", "spread")
_BARS_PER_REQUEST = 900  # Capital.com returns at most 1,000 candles per request.


def _month_starts(start: datetime, end: datetime) -> list[datetime]:
    month = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months = []
    while month < end:
        months.append(month)
        month = (month + timedelta(days=32)).replace(day=1)
    return months


def _write(path: Path, candles: list[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique per process so parallel backtests never rename each other's file.
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(FIELDS)
        for candle in candles:
            writer.writerow(
                [candle.timestamp.isoformat(), candle.open, candle.high, candle.low,
                 candle.close, candle.volume, candle.spread]
            )
    temporary.replace(path)


def _read(path: Path) -> list[Candle]:
    with path.open(newline="", encoding="utf-8") as stream:
        return [
            Candle(
                timestamp=datetime.fromisoformat(row["timestamp"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                spread=float(row["spread"]),
            )
            for row in csv.DictReader(stream)
        ]


def _fetch_range(
    client: CapitalDemoClient,
    epic: str,
    resolution: str,
    start: datetime,
    end: datetime,
) -> list[Candle]:
    step = resolution_delta(resolution) * _BARS_PER_REQUEST
    candles: dict[datetime, Candle] = {}
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + step, end)
        try:
            payload = client._request(
                "GET",
                f"/prices/{epic}",
                params={
                    "resolution": resolution,
                    "from": cursor.strftime("%Y-%m-%dT%H:%M:%S"),
                    "to": chunk_end.strftime("%Y-%m-%dT%H:%M:%S"),
                    "max": 1000,
                },
            ).json()
        except CapitalAPIError as exc:
            # Capital.com answers 404 for a range with no prices at all, e.g. a multi-day
            # market holiday. Treat that as an empty chunk; any other error is real.
            if "HTTP 404" not in str(exc):
                raise
            payload = {"prices": []}
        for item in payload.get("prices") or []:
            candle = _repaired(item)
            if candle is None:
                continue
            if start <= candle.timestamp < end:
                candles[candle.timestamp] = candle
        cursor = chunk_end
        time.sleep(0.12)  # stay well inside Capital.com's request-rate limit
    return [candles[key] for key in sorted(candles)]


def _repaired(item: dict) -> Candle | None:
    """A candle from Capital.com; inconsistent mid prices get high/low set to the OHLC extremes."""
    try:
        return Candle.from_capital(item)
    except ValueError:
        pass
    try:
        values = [_midpoint(item[key]) for key in ("openPrice", "highPrice", "lowPrice", "closePrice")]
        stamp = _parse_timestamp(str(item.get("snapshotTimeUTC") or item.get("snapshotTime")))
        close = item["closePrice"]
        bid, ask = close.get("bid"), close.get("ask")
        spread = float(ask) - float(bid) if bid is not None and ask is not None else 0.0
    except (KeyError, TypeError, ValueError):
        return None
    return Candle(stamp, values[0], max(values), min(values), values[3],
                  float(item.get("lastTradedVolume") or 0.0), spread)


def load_history(
    client: CapitalDemoClient,
    epic: str,
    resolution: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> list[Candle]:
    """Candles in [start, end), cached per calendar month under `cache_dir`."""
    now = datetime.now(timezone.utc)
    result: list[Candle] = []
    for month in _month_starts(start, end):
        month_end = (month + timedelta(days=32)).replace(day=1)
        path = cache_dir / epic / resolution / f"{month:%Y-%m}.csv"
        complete = month_end <= now - timedelta(days=1)
        # A file saved before its month was over lacks the last days: fetch it again.
        fresh = path.exists() and datetime.fromtimestamp(
            path.stat().st_mtime, timezone.utc
        ) >= month_end + timedelta(days=1)
        if complete and fresh:
            candles = _read(path)
        else:
            if progress:
                progress(f"Downloading {epic} {resolution} {month:%Y-%m}")
            candles = _fetch_range(client, epic, resolution, month, min(month_end, now))
            _write(path, candles)
        result.extend(candle for candle in candles if start <= candle.timestamp < end)
    return result
