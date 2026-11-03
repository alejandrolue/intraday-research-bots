from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal


Decision = Literal["NO_TRADE", "WATCH_LONG", "WATCH_SHORT", "READY_LONG", "READY_SHORT"]


def _midpoint(price: dict[str, Any]) -> float:
    bid = price.get("bid")
    ask = price.get("ask")
    if bid is not None and ask is not None:
        return (float(bid) + float(ask)) / 2
    if bid is not None:
        return float(bid)
    if ask is not None:
        return float(ask)
    raise ValueError("Capital.com price object contains neither bid nor ask")


def _parse_timestamp(raw: str) -> datetime:
    value = raw.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    spread: float = 0.0

    def __post_init__(self) -> None:
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("Candle high is below another OHLC value")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("Candle low is above another OHLC value")

    @classmethod
    def from_capital(cls, payload: dict[str, Any]) -> "Candle":
        timestamp = payload.get("snapshotTimeUTC") or payload.get("snapshotTime")
        if not timestamp:
            raise ValueError("Capital.com candle is missing a timestamp")
        close_price = payload["closePrice"]
        bid = close_price.get("bid")
        ask = close_price.get("ask")
        spread = float(ask) - float(bid) if bid is not None and ask is not None else 0.0
        return cls(
            timestamp=_parse_timestamp(str(timestamp)),
            open=_midpoint(payload["openPrice"]),
            high=_midpoint(payload["highPrice"]),
            low=_midpoint(payload["lowPrice"]),
            close=_midpoint(payload["closePrice"]),
            volume=float(payload.get("lastTradedVolume") or 0.0),
            spread=spread,
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["timestamp"] = self.timestamp.isoformat()
        return result


@dataclass(frozen=True)
class MarketSetup:
    decision: Decision
    timestamp: datetime
    reason: str
    bias: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    strategy: str = "unspecified"
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    breakout_level: float | None = None
    zone_low: float | None = None
    zone_high: float | None = None
    atr: float | None = None
    fast_ema: float | None = None
    slow_ema: float | None = None

    @property
    def ready(self) -> bool:
        return self.decision in {"READY_LONG", "READY_SHORT"}

    @property
    def direction(self) -> Literal["BUY", "SELL"] | None:
        if self.decision == "READY_LONG":
            return "BUY"
        if self.decision == "READY_SHORT":
            return "SELL"
        return None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["timestamp"] = self.timestamp.isoformat()
        result["direction"] = self.direction
        return result


@dataclass(frozen=True)
class AIReview:
    verdict: Literal["APPROVE", "REJECT", "NOT_RUN"]
    confidence: float
    concerns: tuple[str, ...]
    rationale: str

    @classmethod
    def not_run(cls, reason: str) -> "AIReview":
        return cls("NOT_RUN", 0.0, (), reason)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AIReview":
        verdict = str(payload["verdict"]).upper()
        if verdict not in {"APPROVE", "REJECT"}:
            raise ValueError(f"Unexpected AI verdict: {verdict}")
        confidence = float(payload["confidence"])
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("AI confidence must be between 0 and 1")
        return cls(
            verdict=verdict,  # type: ignore[arg-type]
            confidence=confidence,
            concerns=tuple(str(item) for item in payload.get("concerns", [])),
            rationale=str(payload["rationale"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "confidence": self.confidence,
            "concerns": list(self.concerns),
            "rationale": self.rationale,
        }
