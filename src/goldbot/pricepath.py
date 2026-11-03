from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .paper import PaperPosition


@dataclass
class _Extremes:
    samples: int
    first_sample: str
    partial: bool
    mfe_r: float
    mfe_at: str
    mae_r: float
    mae_at: str

    def update(self, r: float, timestamp: str) -> None:
        self.samples += 1
        if r > self.mfe_r:
            self.mfe_r, self.mfe_at = r, timestamp
        if r < self.mae_r:
            self.mae_r, self.mae_at = r, timestamp


class PricePathRecorder:
    """Record every open position's live quote each poll, for replaying management rules.

    Lines in the JSONL file:
    - PATH_SAMPLE: the quote and open profit (in R of the initial risk) at one poll.
    - PATH_CLOSE: the broker exit plus the best (MFE) and worst (MAE) open profit seen.
    Profit uses the closing side of the quote (bid for BUY, offer for SELL), the price the
    broker's stop and target trigger on. Samples only exist while the market is open.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._open: dict[str, _Extremes] = {}
        self._started = False
        self._load()

    def record(
        self,
        positions: Iterable[PaperPosition],
        *,
        bid: float,
        offer: float,
        timestamp: str,
    ) -> list[dict[str, Any]]:
        samples: list[dict[str, Any]] = []
        for position in positions:
            price = bid if position.direction == "BUY" else offer
            r = round(position.profit_r(price), 4)
            extremes = self._open.get(position.local_id)
            if extremes is None:
                # A position already open when recording began has no path before that.
                extremes = _Extremes(0, timestamp, not self._started, r, timestamp, r, timestamp)
                self._open[position.local_id] = extremes
            extremes.update(r, timestamp)
            samples.append(
                {
                    "event": "PATH_SAMPLE",
                    "timestamp": timestamp,
                    "local_id": position.local_id,
                    "deal_id": position.deal_id,
                    "strategy": position.strategy,
                    "direction": position.direction,
                    "entry": position.entry,
                    "initial_stop": position.initial_stop,
                    "stop": position.stop,
                    "target": position.target,
                    "bid": bid,
                    "offer": offer,
                    "r": r,
                }
            )
        self._started = True
        self._append(samples)
        return samples

    def mark_started(self) -> None:
        """Positions first seen after this are recorded from their opening."""
        self._started = True

    def finish(self, close_event: dict[str, Any]) -> dict[str, Any] | None:
        """Summarise a closed position's path; returns None if it was never sampled."""
        position = close_event.get("position") or {}
        extremes = self._open.pop(str(position.get("local_id")), None)
        if extremes is None:
            return None
        summary = {
            "mfe_r": extremes.mfe_r,
            "mfe_at": extremes.mfe_at,
            "mae_r": extremes.mae_r,
            "mae_at": extremes.mae_at,
            "samples": extremes.samples,
            "first_sample": extremes.first_sample,
            "partial": extremes.partial,
        }
        self._append(
            [
                {
                    "event": "PATH_CLOSE",
                    "timestamp": close_event.get("timestamp"),
                    "local_id": position.get("local_id"),
                    "deal_id": close_event.get("deal_id") or position.get("deal_id"),
                    "strategy": position.get("strategy"),
                    "direction": position.get("direction"),
                    "entry": position.get("entry"),
                    "initial_stop": position.get("initial_stop"),
                    "target": position.get("target"),
                    "opened_at": position.get("opened_at"),
                    "exit_reason": close_event.get("exit_reason"),
                    "exit_price": close_event.get("exit_price"),
                    "broker_pnl": close_event.get("broker_pnl"),
                    **summary,
                }
            ]
        )
        return summary

    def _load(self) -> None:
        """Resume running extremes after a restart from the file already written."""
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                local_id = str(row.get("local_id"))
                if row.get("event") == "PATH_CLOSE":
                    self._open.pop(local_id, None)
                elif row.get("event") == "PATH_SAMPLE":
                    r, timestamp = float(row["r"]), str(row["timestamp"])
                    extremes = self._open.get(local_id)
                    if extremes is None:
                        extremes = _Extremes(0, timestamp, False, r, timestamp, r, timestamp)
                        self._open[local_id] = extremes
                    extremes.update(r, timestamp)
        # Samples in the file belong to positions this recorder already followed.
        self._started = bool(self._open)

    def _append(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
