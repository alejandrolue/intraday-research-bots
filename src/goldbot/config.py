from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"

# Markets the bot may trade on the demo account, each added only after research and the
# user's approval: GOLD (original), US100 (issue #18 opening-range breakout).
DEMO_EPICS = ("GOLD", "US100")


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True)
class Settings:
    capital_api_key: str | None
    capital_identifier: str | None
    capital_password: str | None
    capital_epic: str | None
    capital_demo_execution_enabled: bool
    capital_max_demo_size: float
    capital_resolution: str
    capital_trend_resolution: str
    capital_price_count: int
    openai_api_key: str | None
    openai_model: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    telegram_allowed_user_id: int | None
    telegram_approval_ttl_seconds: int
    paper_starting_balance: float
    risk_per_trade: float
    max_daily_loss: float
    max_trades_per_day: int
    max_spread: float
    point_value_per_unit: float
    data_dir: Path
    max_open_demo_positions: int = 6
    session: str | None = None
    max_positions_per_session: int = 3
    trail_start_r: float = 1.0
    trail_distance_r: float = 1.0
    target_extend_trigger_r: float = 0.25
    target_extend_r: float = 1.0
    pyramid_at_r: float = 1.0
    trading_hours: str = "london-ny"
    capital_confirm_resolution: str | None = None
    strategies: tuple[str, ...] = ("breakout_retest", "ema_pullback", "demand_zone_rejection")
    manage_trades: bool = True
    minimum_reward_to_risk: float = 1.5
    flat_before_weekend: bool = True
    # Day trading: "<zone> HH:MM", e.g. "Europe/London 16:00" (issue #100).
    flat_at: str | None = None
    # "market" (default) or "stop": broker stop orders for range breakouts (issue #101).
    entry_orders: str = "market"
    record_price_path: bool = False
    open_guard_minutes: int = 15
    cap_daily_risk: bool = False
    trend_filter: str = "ema20_50"
    invert: str | None = None

    @classmethod
    def load(cls, env_file: Path | None = None, data_dir: Path | None = None) -> "Settings":
        load_dotenv(dotenv_path=env_file, override=False)
        resolved_data_dir = data_dir or Path("data")
        return cls(
            capital_api_key=os.getenv("CAPITAL_API_KEY") or None,
            capital_identifier=os.getenv("CAPITAL_IDENTIFIER") or None,
            capital_password=os.getenv("CAPITAL_PASSWORD") or None,
            capital_epic=os.getenv("CAPITAL_EPIC") or None,
            capital_demo_execution_enabled=_bool_env("CAPITAL_DEMO_EXECUTION_ENABLED"),
            capital_max_demo_size=_float_env("CAPITAL_MAX_DEMO_SIZE", 0.1),
            capital_resolution=os.getenv("CAPITAL_RESOLUTION") or "MINUTE_5",
            capital_trend_resolution=os.getenv("CAPITAL_TREND_RESOLUTION") or "HOUR",
            capital_price_count=_int_env("CAPITAL_PRICE_COUNT", 500),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL") or None,
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
            telegram_allowed_user_id=(
                int(os.environ["TELEGRAM_ALLOWED_USER_ID"])
                if os.getenv("TELEGRAM_ALLOWED_USER_ID")
                else None
            ),
            telegram_approval_ttl_seconds=_int_env("TELEGRAM_APPROVAL_TTL_SECONDS", 600),
            paper_starting_balance=_float_env("PAPER_STARTING_BALANCE", 10_000.0),
            risk_per_trade=_float_env("RISK_PER_TRADE", 0.0025),
            max_daily_loss=_float_env("MAX_DAILY_LOSS", 0.01),
            max_trades_per_day=_int_env("MAX_TRADES_PER_DAY", 15),
            max_spread=_float_env("MAX_SPREAD", 0.80),
            point_value_per_unit=_float_env("POINT_VALUE_PER_UNIT", 1.0),
            data_dir=resolved_data_dir,
            max_open_demo_positions=_int_env("MAX_OPEN_DEMO_POSITIONS", 6),
            max_positions_per_session=_int_env("MAX_POSITIONS_PER_SESSION", 3),
            trail_start_r=_float_env("TRAIL_START_R", 1.0),
            trail_distance_r=_float_env("TRAIL_DISTANCE_R", 1.0),
            target_extend_trigger_r=_float_env("TARGET_EXTEND_TRIGGER_R", 0.25),
            target_extend_r=_float_env("TARGET_EXTEND_R", 1.0),
            pyramid_at_r=_float_env("PYRAMID_AT_R", 1.0),
            trading_hours=os.getenv("TRADING_HOURS") or "london-ny",
            open_guard_minutes=_int_env("OPEN_GUARD_MINUTES", 15),
            cap_daily_risk=_bool_env("CAP_DAILY_RISK"),
            capital_confirm_resolution=os.getenv("CAPITAL_CONFIRM_RESOLUTION") or None,
        )

    @property
    def capital_configured(self) -> bool:
        return bool(self.capital_api_key and self.capital_identifier and self.capital_password)

    @property
    def ai_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_model)

    @property
    def telegram_configured(self) -> bool:
        return bool(
            self.telegram_bot_token
            and self.telegram_chat_id
            and self.telegram_allowed_user_id is not None
        )
