from __future__ import annotations

import json

from openai import OpenAI

from .models import AIReview, Candle, MarketSetup


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["APPROVE", "REJECT"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "concerns": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["verdict", "confidence", "concerns", "rationale"],
    "additionalProperties": False,
}


class OpenAISetupReviewer:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def review(self, setup: MarketSetup, candles: list[Candle]) -> AIReview:
        if not setup.ready:
            return AIReview.not_run("AI review is only run for deterministic READY candidates.")

        payload = {
            "setup": setup.to_dict(),
            "recent_completed_candles": [candle.to_dict() for candle in candles[-30:]],
        }
        response = self._client.responses.create(
            model=self._model,
            instructions=(
                "You are a conservative trade-setup reviewer. Review the supplied deterministic "
                "XAU/USD paper-trade candidate for internal consistency, abnormal volatility, "
                "weak confirmation, and poor structure. Reject when uncertain. Do not invent "
                "prices, do not alter the setup, and do not request or execute any trade."
            ),
            input=json.dumps(payload, separators=(",", ":")),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "trade_setup_review",
                    "strict": True,
                    "schema": REVIEW_SCHEMA,
                }
            },
        )
        return AIReview.from_dict(json.loads(response.output_text))
