from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from zoneinfo import ZoneInfo
from pathlib import Path

from .ai_review import OpenAISetupReviewer
from .backtest import (
    BacktestConfig,
    Trade,
    format_backtest_detail,
    format_backtest_table,
    run_backtest,
    with_label,
)
from .capital import CapitalAPIError, CapitalDemoClient
from .commands import CommandService, run_forever
from .config import DEMO_EPICS, Settings
from .control import write_control, write_session_info
from .execution import DemoOrderExecutor
from .volume_pullback import Ema21TrendFilter, VolumePullbackConfig, VolumePullbackStrategy
from .evolution_strategy import DailyEvolutionStrategy, FiveMinuteEvolutionStrategy
from .history import load_history
from .markets import MarketClock
from .lab import (
    LAB_EPICS,
    LabEntry,
    LabRoster,
    PollCache,
    account_balance as lab_account_balance,
    load_roster,
    risk_snapshot as lab_risk_snapshot,
)
from .management import ManagementConfig
from .models import Candle
from .paper import PaperLedger
from .pricepath import PricePathRecorder
from .risk import RiskConfig, RiskEngine
from .runner import BotResult, BotRunner
from .session_strategies import SessionStrategy, SessionVariant
from .range_breakout import RangeBreakoutStrategy
from .stop_orders import StopOrderBracket
from . import daytrade as dt
from .structure_strategies import StructureStrategy, StructureVariant
from .ict_strategies import (
    SilverBulletStrategy,
    SilverBulletVariant,
    TurtleSoupStrategy,
    TurtleSoupVariant,
)
from .sessions import resolution_delta, trading_window
from .stats import format_summary, summarize
from .strategy import (
    DemandZoneStrategy,
    HigherTimeframeTrendFilter,
    PullbackStrategy,
    RetestStrategy,
)
from .telegram import (
    ApprovalQueue,
    TelegramAPIError,
    TelegramApprovalService,
    TelegramBotClient,
    TelegramStatusNotifier,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SESSIONS_DIR = PROJECT_ROOT / "data" / "sessions"
LAB_DIR = PROJECT_ROOT / "data" / "lab"
LAB_ROSTER = PROJECT_ROOT / "lab.json"
DAYTRADE_DIR = PROJECT_ROOT / "data" / "daytrade"
DAYTRADE_ROSTER = PROJECT_ROOT / "daytrade.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="goldbot",
        description="Capital.com demo monitor with guarded paper and demo execution",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="Search demo-account markets")
    search.add_argument("term", nargs="?", default="Gold")

    once = subparsers.add_parser("once", help="Analyze the latest configured completed bar")
    once.add_argument("--ai", action="store_true", help="Enable optional OpenAI review")
    once.add_argument("--paper", action="store_true", help="Update the local paper ledger")
    once.add_argument(
        "--demo-execute",
        action="store_true",
        help="Automatically submit approved setups to the Capital.com demo account",
    )

    _add_session_options(once)

    watch = subparsers.add_parser("watch", help="Monitor for each newly completed bar")
    watch.add_argument("--interval", type=int, default=60, help="Polling interval in seconds")
    watch.add_argument("--iterations", type=int, default=0, help="Stop after N polls; 0 runs forever")
    watch.add_argument("--ai", action="store_true", help="Enable optional OpenAI review")
    watch.add_argument("--paper", action="store_true", help="Update the local paper ledger")
    watch.add_argument(
        "--telegram",
        action="store_true",
        help="Send READY paper proposals to Telegram for approval",
    )
    watch.add_argument(
        "--demo-execute",
        action="store_true",
        help="Automatically submit approved setups to the Capital.com demo account",
    )
    watch.add_argument(
        "--telegram-status",
        action="store_true",
        help="Send bot start, positions opened/closed, broker rejections and errors to Telegram",
    )
    watch.add_argument(
        "--telegram-decisions",
        action="store_true",
        help="Also send every candle's analysis to Telegram (noisy; off by default)",
    )

    _add_session_options(watch)

    backtest = subparsers.add_parser(
        "backtest", help="Replay the strategies on Capital.com history with spread costs"
    )
    backtest.add_argument("--resolution", default="MINUTE_5", help="Entry candles")
    backtest.add_argument(
        "--trend-resolution", default="HOUR", help="Trend-filter candles, or 'none'"
    )
    backtest.add_argument("--days", type=int, default=180, help="How far back to test")
    backtest.add_argument("--hours", choices=("london-ny", "all"), default="london-ny")
    backtest.add_argument(
        "--no-manage", action="store_true", help="Fixed stop/target: no trailing or adding"
    )
    backtest.add_argument("--max-positions", type=int, default=3)
    backtest.add_argument(
        "--strategies",
        default="breakout_retest,ema_pullback,demand_zone_rejection",
        help="Comma-separated strategy names",
    )
    backtest.add_argument(
        "--compare",
        action="store_true",
        help="Run every trend filter x trading hours x management combination",
    )
    backtest.add_argument("--trades-csv", type=Path, help="Write the baseline run's trades here")

    stats = subparsers.add_parser("stats", help="Compare closed-trade performance per session")
    stats.add_argument("sessions", nargs="*", help="Session names; default is every session")
    stats.add_argument("--telegram", action="store_true", help="Also send the summary to Telegram")

    subparsers.add_parser(
        "demo-check",
        help="Validate demo execution settings and show non-sensitive market constraints",
    )

    subparsers.add_parser(
        "telegram-discover",
        help="Show chat and user IDs from recent messages sent to the configured bot",
    )
    subparsers.add_parser("telegram-test", help="Send a test message to the configured chat")
    lab = subparsers.add_parser(
        "lab", help="Forward-test the lab roster on the lab demo account (issue #24)"
    )
    lab.add_argument("--roster", type=Path, default=LAB_ROSTER, help="Roster JSON file")
    lab.add_argument("--iterations", type=int, default=0, help="Stop after N polls; 0 runs forever")
    lab.add_argument("--check", action="store_true", help="Validate the roster and exit")
    daytrade = subparsers.add_parser(
        "daytrade", help="Day-trading portfolio: all passed intraday rules on one demo account (issue #166)"
    )
    daytrade.add_argument("--roster", type=Path, default=DAYTRADE_ROSTER, help="Roster JSON file")
    daytrade.add_argument("--iterations", type=int, default=0, help="Stop after N polls; 0 runs forever")
    daytrade.add_argument("--check", action="store_true", help="Validate the roster and print the lanes")

    subparsers.add_parser(
        "telegram-commands",
        help="Answer /status, /stats, /pause, /resume and /closeall from Telegram",
    )

    replay = subparsers.add_parser("replay", help="Analyze completed candles from a CSV file")
    replay.add_argument("file", type=Path)
    return parser


def _add_session_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--session",
        help="Run as a named session with its own ledger under data/sessions/<name>",
    )
    parser.add_argument("--resolution", help="Entry candles, e.g. MINUTE_5 or HOUR")
    parser.add_argument("--trend-resolution", help="Trend-filter candles, e.g. HOUR or HOUR_4")
    parser.add_argument(
        "--confirm-trend-resolution",
        help="A second trend that must also agree, e.g. DAY; 'none' disables it",
    )
    parser.add_argument(
        "--account",
        help="Capital.com demo sub-account name or ID to trade on (default: preferred account)",
    )
    parser.add_argument(
        "--starting-balance",
        type=float,
        help="Starting balance for a new session ledger",
    )
    parser.add_argument(
        "--hours",
        choices=("london-ny", "all"),
        help="Entry hours: london-ny (default) or all",
    )
    parser.add_argument(
        "--strategies",
        help="Comma-separated strategies, e.g. ema_pullback or orb-nycash-15-rr2",
    )
    parser.add_argument("--epic", help="Capital.com market to trade, e.g. GOLD or US100")
    parser.add_argument(
        "--max-spread", type=float, help="Skip entries when the spread is above this (price points)"
    )
    parser.add_argument(
        "--risk-per-trade", type=float, help="Share of the balance risked per trade, e.g. 0.01"
    )
    parser.add_argument("--max-size", type=float, help="Largest position size in units")
    parser.add_argument(
        "--max-daily-loss", type=float, help="Stop new entries after this daily loss, e.g. 0.10"
    )
    parser.add_argument(
        "--trail-start-r", type=float, help="Trade manager: move the stop to breakeven at this profit (R)"
    )
    parser.add_argument(
        "--trail-distance-r", type=float, help="Trade manager: trailing stop distance behind price (R)"
    )
    parser.add_argument(
        "--no-adds", action="store_true", help="Trade manager without adding to winners"
    )
    parser.add_argument(
        "--invert",
        choices=("mirror", "exact", "trend-keep"),
        help="Trade the opposite of each signal (issue #69); off by default",
    )
    parser.add_argument(
        "--trend-filter",
        choices=("ema20_50", "ema21_spec"),
        help="Higher-timeframe bias rule (default ema20_50; ema21_spec for issue #63)",
    )
    parser.add_argument(
        "--price-count", type=int, help="Candles fetched per request, 100-1000 (default 500)"
    )
    parser.add_argument(
        "--cap-daily-risk",
        action="store_true",
        help="Count open stop-risk with realized losses against the daily-loss limit",
    )
    parser.add_argument(
        "--min-rr", type=float, help="Smallest reward-to-risk a setup may have (default 1.5)"
    )
    parser.add_argument(
        "--entry-orders",
        choices=("market", "stop"),
        help="stop: enter range breakouts with broker stop orders at the range edges (issue #101)",
    )
    parser.add_argument(
        "--flat-at",
        help="Close own positions and stop new entries at this local time each day, e.g. 'Europe/London 16:00'",
    )
    parser.add_argument(
        "--hold-over-weekend",
        action="store_true",
        help="Keep positions over the weekend (default: no entries in the last hour, flat 15 min before)",
    )
    parser.add_argument("--max-positions", type=int, help="Open positions allowed at once")
    parser.add_argument(
        "--max-account-positions", type=int,
        help="Cap on open positions across the whole demo account (default MAX_OPEN_DEMO_POSITIONS, 6)",
    )
    parser.add_argument("--max-trades-per-day", type=int, help="New entries allowed per UTC day")
    parser.add_argument(
        "--no-manage",
        action="store_true",
        help="Fixed stop and target: no breakeven, trailing, target extension or adds",
    )
    parser.add_argument(
        "--record-path",
        action="store_true",
        help="Save each open position's live quote every poll (price_paths.jsonl)",
    )


def _settings(args: argparse.Namespace | None = None) -> Settings:
    settings = Settings.load(env_file=PROJECT_ROOT / ".env", data_dir=PROJECT_ROOT / "data")
    session = getattr(args, "session", None)
    if session:
        if not session.replace("-", "").replace("_", "").isalnum():
            raise ValueError("--session may contain only letters, digits, - and _")
        settings = replace(
            settings,
            session=session,
            data_dir=SESSIONS_DIR / session,
        )
    overrides = {
        "capital_resolution": getattr(args, "resolution", None),
        "capital_trend_resolution": getattr(args, "trend_resolution", None),
        "paper_starting_balance": getattr(args, "starting_balance", None),
        "trading_hours": getattr(args, "hours", None),
        "capital_confirm_resolution": getattr(args, "confirm_trend_resolution", None),
        "capital_epic": getattr(args, "epic", None),
        "max_spread": getattr(args, "max_spread", None),
        "risk_per_trade": getattr(args, "risk_per_trade", None),
        "capital_max_demo_size": getattr(args, "max_size", None),
        "max_daily_loss": getattr(args, "max_daily_loss", None),
        "cap_daily_risk": True if getattr(args, "cap_daily_risk", False) else None,
        "trend_filter": getattr(args, "trend_filter", None),
        "invert": getattr(args, "invert", None),
        "trail_start_r": getattr(args, "trail_start_r", None),
        "trail_distance_r": getattr(args, "trail_distance_r", None),
        # Adding to winners triggers at pyramid_at_r; far beyond any target means never.
        "pyramid_at_r": 1e9 if getattr(args, "no_adds", False) else None,
        "capital_price_count": getattr(args, "price_count", None),
        "minimum_reward_to_risk": getattr(args, "min_rr", None),
        "flat_before_weekend": False if getattr(args, "hold_over_weekend", False) else None,
        "flat_at": getattr(args, "flat_at", None),
        "entry_orders": getattr(args, "entry_orders", None),
        "max_positions_per_session": getattr(args, "max_positions", None),
        "max_open_demo_positions": getattr(args, "max_account_positions", None),
        "max_trades_per_day": getattr(args, "max_trades_per_day", None),
        "strategies": (
            tuple(name.strip() for name in args.strategies.split(",") if name.strip())
            if getattr(args, "strategies", None)
            else None
        ),
        "manage_trades": False if getattr(args, "no_manage", False) else None,
        "record_price_path": True if getattr(args, "record_path", False) else None,
    }
    settings = replace(
        settings, **{key: value for key, value in overrides.items() if value is not None}
    )
    if (settings.capital_trend_resolution or "").lower() == "none":
        settings = replace(settings, capital_trend_resolution="")
    if settings.flat_at:
        parse_flat_at(settings.flat_at)  # reject a bad value early
    if settings.entry_orders == "stop" and not (
        len(settings.strategies) == 1 and settings.strategies[0].startswith("rangebreak-")
    ):
        raise ValueError("--entry-orders stop needs exactly one rangebreak-* strategy")
    if (settings.capital_confirm_resolution or "").lower() == "none":
        settings = replace(settings, capital_confirm_resolution=None)
    if settings.capital_confirm_resolution:
        resolution_delta(settings.capital_confirm_resolution)  # reject unknown resolutions early
    _strategies(settings.strategies, settings.capital_resolution)  # reject bad names early
    if not 0 < settings.risk_per_trade <= 0.05:
        raise ValueError("--risk-per-trade must be above 0 and at most 0.05 (5%)")
    if not 0 < settings.max_daily_loss <= 0.25:
        raise ValueError("--max-daily-loss must be above 0 and at most 0.25 (25%)")
    if settings.capital_max_demo_size <= 0:
        raise ValueError("--max-size must be positive")
    if not 100 <= settings.capital_price_count <= 1000:
        raise ValueError("--price-count must be between 100 and 1000")
    if not 1.0 <= settings.minimum_reward_to_risk <= 5.0:
        raise ValueError("--min-rr must be between 1.0 and 5.0")
    return settings


def _capital_client(settings: Settings) -> CapitalDemoClient:
    if not settings.capital_configured:
        raise RuntimeError(
            "Capital.com demo credentials are missing. Fill CAPITAL_API_KEY, "
            "CAPITAL_IDENTIFIER, and CAPITAL_PASSWORD in .env."
        )
    assert settings.capital_api_key and settings.capital_identifier and settings.capital_password
    return CapitalDemoClient(
        settings.capital_api_key,
        settings.capital_identifier,
        settings.capital_password,
    )


def _runner(settings: Settings, *, use_ai: bool) -> BotRunner:
    reviewer = None
    if use_ai:
        if not settings.ai_configured:
            raise RuntimeError("--ai requires OPENAI_API_KEY and OPENAI_MODEL in .env")
        assert settings.openai_api_key and settings.openai_model
        reviewer = OpenAISetupReviewer(settings.openai_api_key, settings.openai_model)

    ledger = PaperLedger(
        settings.data_dir / "paper_state.json",
        settings.data_dir / "journal.jsonl",
        starting_balance=settings.paper_starting_balance,
        point_value_per_unit=settings.point_value_per_unit,
    )
    risk_engine = RiskEngine(
        RiskConfig(
            risk_per_trade=settings.risk_per_trade,
            max_daily_loss=settings.max_daily_loss,
            max_trades_per_day=settings.max_trades_per_day,
            max_spread=settings.max_spread,
            point_value_per_unit=settings.point_value_per_unit,
            max_positions=settings.max_positions_per_session,
            minimum_reward_to_risk=settings.minimum_reward_to_risk,
            cap_daily_risk=settings.cap_daily_risk,
        )
    )
    return BotRunner(
        _strategies(settings.strategies, settings.capital_resolution),
        risk_engine,
        ledger,
        reviewer,
        (
            (Ema21TrendFilter() if settings.trend_filter == "ema21_spec" else HigherTimeframeTrendFilter())
            if settings.capital_trend_resolution
            else None
        ),
        entry_window=trading_window(
            settings.trading_hours, guard_minutes=settings.open_guard_minutes
        ),
        entry_duration=resolution_delta(settings.capital_resolution),
        confirm_filter=(
            HigherTimeframeTrendFilter() if settings.capital_confirm_resolution else None
        ),
        control_dir=settings.data_dir if settings.session else None,
        invert=settings.invert,
    )


def _telegram_client(settings: Settings, *, require_ids: bool) -> TelegramBotClient:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing from .env")
    if require_ids and not settings.telegram_configured:
        raise RuntimeError(
            "Telegram approval requires TELEGRAM_CHAT_ID and TELEGRAM_ALLOWED_USER_ID in .env"
        )
    return TelegramBotClient(settings.telegram_bot_token)


def _telegram_service(
    settings: Settings,
    client: TelegramBotClient,
    runner: BotRunner,
) -> TelegramApprovalService:
    assert settings.telegram_chat_id and settings.telegram_allowed_user_id is not None
    return TelegramApprovalService(
        client,
        chat_id=settings.telegram_chat_id,
        allowed_user_id=settings.telegram_allowed_user_id,
        queue=ApprovalQueue(settings.data_dir / "telegram_state.json"),
        ledger=runner.ledger,
        risk_engine=runner.risk_engine,
        approval_ttl_seconds=settings.telegram_approval_ttl_seconds,
    )


# Sessions allowed to trade beyond DEMO_EPICS, each only on its own dedicated demo account.
# Demo accounts are matched by their fixed Capital.com ID, so renaming an account never breaks a bot.
DAILY_FAKEOUT_ACCOUNT_ID = "000000000000000001"
SESSION_EPICS = {
    # issue #60: the daily fakeout demo experiment
    "daily-fakeout-": (("GOLD", "US100", "US500", "US30"), DAILY_FAKEOUT_ACCOUNT_ID),
    # issue #103: the #31 US500 1-minute pass in its own forward test
    "us500-orbmid30": (("US500",), DAILY_FAKEOUT_ACCOUNT_ID),
}


def _account_matches(account: dict | str | None, wanted: str) -> bool:
    """True when `account` (a broker account dict, or an ID/name string) is the account `wanted` (ID or name)."""
    if isinstance(account, dict):
        candidates = {str(account.get("accountId") or ""), str(account.get("accountName") or "").strip().lower()}
    else:
        candidates = {str(account or ""), str(account or "").strip().lower()}
    return str(wanted) in candidates or str(wanted).strip().lower() in candidates


def _session_epics(settings: Settings, account: dict | str | None) -> tuple[str, ...]:
    for prefix, (epics, required_account) in SESSION_EPICS.items():
        if (settings.session or "").startswith(prefix):
            if not _account_matches(account, required_account):
                raise RuntimeError(
                    f"Session {settings.session} may only trade on demo account {required_account!r}"
                )
            return epics
    return DEMO_EPICS


def _demo_executor(
    settings: Settings,
    client: CapitalDemoClient,
    runner: BotRunner,
    *,
    allowed_epics: tuple[str, ...] = DEMO_EPICS,
    use_minimum_size: bool = False,
) -> DemoOrderExecutor:
    if not settings.capital_demo_execution_enabled:
        raise RuntimeError(
            "Demo execution is locked. Set CAPITAL_DEMO_EXECUTION_ENABLED=true in .env."
        )
    if settings.capital_epic not in allowed_epics:
        raise RuntimeError(
            f"Demo execution is restricted to approved markets: {', '.join(allowed_epics)}"
        )
    return DemoOrderExecutor(
        client,
        runner.ledger,
        settings.data_dir / "demo_execution.jsonl",
        allowed_epic=settings.capital_epic,
        max_size=settings.capital_max_demo_size,
        max_open_positions=settings.max_open_demo_positions,
        hedging_mode=bool(client.get_preferences().get("hedgingMode")),
        management=ManagementConfig(
            trail_start_r=settings.trail_start_r,
            trail_distance_r=settings.trail_distance_r,
            target_extend_trigger_r=settings.target_extend_trigger_r,
            target_extend_r=settings.target_extend_r,
            pyramid_at_r=settings.pyramid_at_r,
        ),
        use_minimum_size=use_minimum_size,
    )


def _strategies(names: tuple[str, ...], resolution: str = "MINUTE_5") -> tuple[object, ...]:
    built = {
        "5m_evolution": FiveMinuteEvolutionStrategy,
        "5m_evolution_daily": DailyEvolutionStrategy,
        "breakout_retest": RetestStrategy,
        "ema_pullback": PullbackStrategy,
        "demand_zone_rejection": DemandZoneStrategy,
    }
    def build(name: str) -> object:
        if name in built:
            return built[name]()
        if name.startswith("evp-"):
            return VolumePullbackStrategy(VolumePullbackConfig.parse(name))
        if name.startswith("structure_"):
            return StructureStrategy(StructureVariant.parse(name))
        if name.startswith("silverbullet-"):
            bar_minutes = int(resolution_delta(resolution).total_seconds() // 60)
            return SilverBulletStrategy(SilverBulletVariant.parse(name), bar_minutes=bar_minutes)
        if name.startswith("rangebreak-"):
            return RangeBreakoutStrategy(name)
        if name.startswith("turtlesoup-"):
            return TurtleSoupStrategy(TurtleSoupVariant.parse(name))
        bar_minutes = int(resolution_delta(resolution).total_seconds() // 60)
        return SessionStrategy(SessionVariant.parse(name), bar_minutes=bar_minutes)

    return tuple(build(name) for name in names)


def _manage_positions(
    demo_executor: DemoOrderExecutor,
    runner: BotRunner,
    notifier: TelegramStatusNotifier | None,
    *,
    manage: bool = True,
    recorder: PricePathRecorder | None = None,
    block_adds: bool = False,
    max_risk_budget: float | None = None,
) -> None:
    """Sync closed positions from the broker, then trail, extend, and add to winners."""
    for event in demo_executor.reconcile():
        _print(event)
        if event.get("event") != "PAPER_CLOSE":
            continue  # a partial fill: the final close reports the combined result
        _attach_path(recorder, event)
        _notify_status(notifier, "trade_closed", event)
    if not manage:
        return  # fixed stop and target, as in a research configuration
    state = runner.ledger.risk_state(datetime.now(timezone.utc))
    allow_add = (
        not block_adds
        and runner.paused_reason() is None
        and state.open_positions > 0
        and runner.risk_engine.entry_block_reason(state, state.open_direction) is None
    )
    for event in demo_executor.manage(
        allow_add=allow_add, max_risk_budget=max_risk_budget
    ):
        _print(event)
        if event.get("event") == "PAPER_OPEN":
            _notify_status(
                notifier, "trade_opened", event, deal_id=event["position"].get("deal_id")
            )
        # Stop/target adjustments are logged only; Telegram gets opens and closes.


def _attach_path(recorder: PricePathRecorder | None, close_event: dict[str, object]) -> None:
    summary = recorder.finish(close_event) if recorder else None
    if summary:
        close_event["path"] = summary


def _record_price_path(
    client: CapitalDemoClient, epic: str, ledger: PaperLedger, recorder: PricePathRecorder
) -> None:
    """One quote per poll for this session's open positions (none while flat)."""
    positions = [item for item in ledger.open_positions if item.deal_id]
    if not positions:
        recorder.mark_started()
        return
    snapshot = client.get_market_details(epic).get("snapshot") or {}
    if snapshot.get("marketStatus") != "TRADEABLE" or not snapshot.get("bid"):
        return
    recorder.record(
        positions,
        bid=float(snapshot["bid"]),
        offer=float(snapshot["offer"]),
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


WEEKEND_NO_ENTRIES = timedelta(minutes=60)
WEEKEND_FLATTEN = timedelta(minutes=15)


def _weekend_guard(
    market_clock: MarketClock,
    epic: str,
    now: datetime,
    runner: BotRunner,
    executor: DemoOrderExecutor | None,
    notifier: TelegramStatusNotifier | None,
    *,
    enabled: bool,
) -> None:
    """Before the weekend close: block new entries for the last hour, go flat 15 minutes before."""
    runner.entry_block = None
    if not enabled:
        return
    closes = market_clock.weekend_close(epic, now)
    if closes is None or closes - now > WEEKEND_NO_ENTRIES:
        return
    minutes = max(0, int((closes - now).total_seconds() // 60))
    runner.entry_block = f"Weekend close in {minutes} minutes: no new entries."
    if executor is not None and closes - now <= WEEKEND_FLATTEN and runner.ledger.open_positions:
        for event in executor.close_all("WEEKEND_CLOSE"):
            _print(event)
        if notifier is not None:
            try:
                notifier.client.send_message(
                    notifier.chat_id,
                    (f"[{notifier.label}] " if notifier.label else "")
                    + f"🏁 Weekend close: closing open positions ({minutes} min before the market closes).",
                )
            except TelegramAPIError as exc:
                _print({"warning": f"Telegram weekend-close message failed: {exc}"})


def parse_flat_at(text: str) -> tuple[ZoneInfo, dt_time]:
    """'Europe/London 16:00' -> (zone, 16:00)."""
    try:
        zone, clock = text.split()
        hour, minute = (int(part) for part in clock.split(":"))
        return ZoneInfo(zone), dt_time(hour, minute)
    except (ValueError, KeyError) as exc:
        raise ValueError(f"--flat-at must look like 'Europe/London 16:00', got {text!r}") from exc


def _daily_flat_guard(
    flat_at: str | None,
    now: datetime,
    runner: BotRunner,
    executor: DemoOrderExecutor | None,
    notifier: TelegramStatusNotifier | None,
) -> None:
    """Day trading (issue #100): from the flat time to local midnight, no entries and no open positions."""
    if not flat_at:
        return
    zone, at = parse_flat_at(flat_at)
    if now.astimezone(zone).time() < at:
        return
    runner.entry_block = runner.entry_block or f"After the daily close ({flat_at}): no new entries."
    if executor is not None and runner.ledger.open_positions:
        for event in executor.close_all("END_OF_DAY"):
            _print(event)
        if notifier is not None:
            try:
                notifier.client.send_message(
                    notifier.chat_id,
                    (f"[{notifier.label}] " if notifier.label else "")
                    + f"🌙 Daily close ({flat_at}): closing open positions.",
                )
            except TelegramAPIError as exc:
                _print({"warning": f"Telegram daily-close message failed: {exc}"})


def _worth_notifying(status: str) -> bool:
    """Blocked setups (hours, limits, drift) are routine; a broker rejection is not."""
    return status == "REJECTED"


def _notify_status(
    notifier: TelegramStatusNotifier | None,
    method: str,
    *args: object,
    **kwargs: object,
) -> None:
    if notifier is None:
        return
    try:
        getattr(notifier, method)(*args, **kwargs)
    except TelegramAPIError as exc:
        _print({"warning": f"Telegram status notification failed: {exc}"})


def _run_once(
    client: CapitalDemoClient,
    settings: Settings,
    runner: BotRunner,
    *,
    use_ai: bool,
    paper: bool,
    skip_timestamp: str | None = None,
    defer_paper_entry: bool = False,
) -> tuple[str, BotResult | None]:
    if not settings.capital_epic:
        raise RuntimeError("CAPITAL_EPIC is missing. Run `goldbot search Gold` first.")
    resolution = getattr(settings, "capital_resolution", "MINUTE")
    max_count = getattr(settings, "capital_price_count", 500)
    candles, _ = client.get_prices(
        settings.capital_epic,
        resolution=resolution,
        max_count=max_count,
    )
    completed_timestamp = candles[-2].timestamp.isoformat()
    if completed_timestamp == skip_timestamp:
        return completed_timestamp, None
    trend_resolution = getattr(settings, "capital_trend_resolution", "HOUR")
    trend_candles = None
    if trend_resolution:
        trend_candles, _ = client.get_prices(
            settings.capital_epic,
            resolution=trend_resolution,
            max_count=max_count,
        )
    confirm_candles = None
    confirm_resolution = getattr(settings, "capital_confirm_resolution", None)
    if confirm_resolution:
        confirm_candles, _ = client.get_prices(
            settings.capital_epic,
            resolution=confirm_resolution,
            max_count=max_count,
        )
    result = runner.process(
        candles,
        use_ai=use_ai,
        paper=paper,
        defer_paper_entry=defer_paper_entry,
        trend_candles=trend_candles,
        confirm_candles=confirm_candles,
    )
    return completed_timestamp, result


_COMPARE_FILTERS = {
    "MINUTE_5": ("HOUR", "MINUTE_30", None),
    "MINUTE_15": ("HOUR", "HOUR_4", None),
    "MINUTE_30": ("HOUR_4", "HOUR", None),
    "HOUR": ("HOUR_4", "DAY", None),
}


def _run_config(job: tuple[BacktestConfig, list[Candle], list[Candle] | None]) -> dict[str, object]:
    config, candles, trend_candles = job
    result = run_backtest(candles, trend_candles, config)
    return {"metrics": result.metrics(), "trades": result.trades}


def _backtest_command(args: argparse.Namespace, settings: Settings) -> int:
    from concurrent.futures import ProcessPoolExecutor

    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=args.days)
    trend = None if args.trend_resolution.lower() == "none" else args.trend_resolution
    base = BacktestConfig(
        resolution=args.resolution,
        trend_resolution=trend,
        window=trading_window(args.hours, guard_minutes=settings.open_guard_minutes),
        management=None
        if args.no_manage
        else ManagementConfig(
            trail_start_r=settings.trail_start_r,
            trail_distance_r=settings.trail_distance_r,
            target_extend_trigger_r=settings.target_extend_trigger_r,
            target_extend_r=settings.target_extend_r,
            pyramid_at_r=settings.pyramid_at_r,
        ),
        risk=RiskConfig(
            risk_per_trade=settings.risk_per_trade,
            max_daily_loss=settings.max_daily_loss,
            max_trades_per_day=settings.max_trades_per_day,
            max_spread=settings.max_spread,
            point_value_per_unit=settings.point_value_per_unit,
            max_positions=args.max_positions,
            cap_daily_risk=settings.cap_daily_risk,
        ),
        strategies=tuple(name.strip() for name in args.strategies.split(",") if name.strip()),
        units=settings.capital_max_demo_size,
        starting_balance=settings.paper_starting_balance,
    )
    configs = [with_label(base)]
    if args.compare:
        configs = [
            with_label(base, trend_resolution=filter_resolution, window=window, management=manage)
            for filter_resolution in _COMPARE_FILTERS.get(args.resolution, (trend,))
            for window in (base.window or trading_window("london-ny"), None)
            for manage in (base.management or ManagementConfig(), None)
        ]

    cache = PROJECT_ROOT / "data" / "history"
    entry_warmup = resolution_delta(args.resolution) * (base.lookback + 50)
    with _capital_client(settings) as client:
        def fetch(resolution: str, warmup: timedelta) -> list[Candle]:
            return load_history(
                client, settings.capital_epic or "GOLD", resolution, start - warmup, end, cache,
                progress=lambda message: print(message, file=sys.stderr),
            )

        candles = fetch(args.resolution, entry_warmup)
        trend_sets: dict[str, list[Candle]] = {}
        for config in configs:
            if config.trend_resolution and config.trend_resolution not in trend_sets:
                trend_sets[config.trend_resolution] = fetch(
                    config.trend_resolution,
                    resolution_delta(config.trend_resolution) * (base.trend_lookback + 10)
                    + entry_warmup,
                )
    print(
        f"Backtest {settings.capital_epic or 'GOLD'} {args.resolution}: "
        f"{candles[0].timestamp:%Y-%m-%d} to {candles[-1].timestamp:%Y-%m-%d}, "
        f"{len(candles):,} candles, {len(configs)} configuration(s)",
        file=sys.stderr,
    )
    jobs = [
        (config, candles, trend_sets.get(config.trend_resolution or "")) for config in configs
    ]
    with ProcessPoolExecutor() as pool:
        outcomes = list(pool.map(_run_config, jobs))

    rows = [outcome["metrics"] for outcome in outcomes]
    print(format_backtest_table(rows))
    print()
    print(format_backtest_detail(rows[0]))

    report_dir = PROJECT_ROOT / "data" / "backtests"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"{end:%Y%m%dT%H%M}-{args.resolution}-{args.days}d.json"
    report.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    print(f"\nReport: {report}")
    if args.trades_csv:
        with args.trades_csv.open("w", newline="", encoding="utf-8") as stream:
            trades = outcomes[0]["trades"]
            writer = csv.writer(stream)
            writer.writerow(Trade.__dataclass_fields__)
            for trade in trades:  # type: ignore[attr-defined]
                writer.writerow(asdict(trade).values())
        print(f"Trades: {args.trades_csv}")
    return 0


def _lab_settings(base: Settings, roster: LabRoster, entry: LabEntry) -> Settings:
    return replace(
        base,
        session=f"lab-{entry.name}",
        data_dir=LAB_DIR / entry.name,
        capital_epic=entry.epic,
        capital_resolution=entry.resolution,
        capital_trend_resolution=entry.trend_resolution,
        capital_confirm_resolution=entry.confirm_resolution,
        strategies=entry.strategies,
        max_positions_per_session=entry.max_positions,
        max_trades_per_day=entry.max_trades_per_day,
        manage_trades=entry.manage,
        trading_hours=entry.hours,
        max_spread=entry.max_spread,
        paper_starting_balance=roster.starting_balance,
        risk_per_trade=roster.risk_per_trade,
        capital_max_demo_size=roster.max_size,
        max_daily_loss=roster.max_daily_loss,
        max_open_demo_positions=roster.max_open_positions,
        minimum_reward_to_risk=entry.min_rr,
        flat_at=entry.flat_at,
    )


def _daytrade_settings(base: Settings, roster: "dt.DayRoster", lane: "dt.DayLane") -> Settings:
    return replace(
        base,
        session=f"dt-{lane.name}",
        data_dir=DAYTRADE_DIR / lane.name,
        capital_epic=lane.epic,
        capital_resolution=lane.resolution,
        capital_trend_resolution=lane.trend_resolution,
        capital_confirm_resolution=None,
        capital_price_count=lane.price_count,
        strategies=(lane.strategy,),
        max_positions_per_session=1,
        max_trades_per_day=lane.max_trades_per_day,
        manage_trades=lane.trail,
        trail_start_r=1.0,
        trail_distance_r=1.0,
        pyramid_at_r=1e9,                       # no adds
        trading_hours=lane.hours,
        max_spread=lane.max_spread,
        paper_starting_balance=roster.starting_balance,
        # the engine may size up to the largest multiplier; the per-entry budget below sets the real risk
        risk_per_trade=roster.risk_per_trade * roster.max_multiplier,
        capital_max_demo_size=roster.max_size,
        max_daily_loss=0.05,
        max_open_demo_positions=12,
        minimum_reward_to_risk=lane.min_rr,
        flat_at=lane.flat_at,
        entry_orders=lane.entry_orders,
        record_price_path=False,
    )


def _daytrade_command(args: argparse.Namespace, settings: Settings) -> int:
    """Issue #166 Part C: one process, one lane per passed intraday rule, shared risk (rules pre-registered on #166)."""
    roster = dt.load_roster(args.roster)
    if args.check:
        _print({"account": roster.account, "lanes": [lane.name for lane in roster.lanes],
                "risk_per_trade": roster.risk_per_trade, "daily_limit": roster.max_account_daily_loss,
                "open_risk_cap": roster.max_account_open_risk})
        return 0
    if not settings.capital_demo_execution_enabled:
        raise RuntimeError("Demo execution is locked. Set CAPITAL_DEMO_EXECUTION_ENABLED=true in .env.")
    telegram: TelegramBotClient | None = None
    if settings.telegram_configured:
        telegram = _telegram_client(settings, require_ids=True)

    def say(text: str) -> None:
        if telegram and settings.telegram_chat_id:
            try:
                telegram.send_message(settings.telegram_chat_id, text)
            except TelegramAPIError as exc:
                _print({"warning": f"Telegram day-trading message failed: {exc}"})

    with _capital_client(settings) as raw:
        account = raw.use_account(roster.account)
        if not _account_matches(account, roster.account):
            raise RuntimeError(f"The day-trading portfolio must run on demo account {roster.account!r}")
        client = PollCache(raw)
        lanes = []
        for lane in roster.lanes:
            lane_settings = _daytrade_settings(settings, roster, lane)
            runner = _runner(lane_settings, use_ai=False)
            executor = _demo_executor(lane_settings, client, runner, allowed_epics=dt.DAYTRADE_EPICS)  # type: ignore[arg-type]
            notifier = (
                TelegramStatusNotifier(telegram, chat_id=str(settings.telegram_chat_id), label=f"dt:{lane.name}",
                                       market="Gold" if lane.epic == "GOLD" else lane.epic)
                if telegram else None
            )
            bracket = (
                StopOrderBracket(client, executor, runner.ledger, runner.risk_engine,
                                 lane_settings.data_dir / "bracket.json", epic=lane.epic, notifier=notifier)
                if lane.entry_orders == "stop" else None
            )
            write_session_info(lane_settings.data_dir, {
                "session": lane_settings.session, "daytrade": True, "epic": lane.epic,
                "account_id": account.get("accountId"), "account_name": account.get("accountName"),
                "resolution": lane.resolution, "trend_resolution": lane.trend_resolution,
                "strategies": [lane.strategy], "flat_at": lane.flat_at, "entry_orders": lane.entry_orders,
                "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            lanes.append({"lane": lane, "settings": lane_settings, "runner": runner, "executor": executor,
                          "notifier": notifier, "bracket": bracket,
                          "last": runner.ledger.state.last_processed_timestamp})
        ledgers = [item["runner"].ledger for item in lanes]
        lane_dirs = {item["lane"].name: item["settings"].data_dir for item in lanes}
        clock = dt.MessageClock(DAYTRADE_DIR / "messages.json")
        paused_alerted: set[str] = set()
        _print({"event": "DAYTRADE_STARTED", "account": account.get("accountName"),
                "lanes": [lane.name for lane in roster.lanes]})
        say(f"📈 Day-trading portfolio started on demo '{account.get('accountName')}'\n"
            f"{len(lanes)} rules: " + ", ".join(lane.name for lane in roster.lanes)
            + f"\nRisk {roster.risk_per_trade:.0%} per trade × edge multiplier · daily limit −{roster.max_account_daily_loss:.0%}"
            + f" · open-risk cap {roster.max_account_open_risk:.0%}")
        polls = 0
        market_clock = MarketClock(getattr(raw, "get_market_details", None))
        while args.iterations == 0 or polls < args.iterations:
            polls += 1
            client.new_poll()
            now = datetime.now(timezone.utc)
            reopening = {epic: market_clock.closed_until(epic, now) for epic in {i["lane"].epic for i in lanes}}
            balance: float | None = None
            if any(v is None for v in reopening.values()) or clock.due("morning", now) or clock.due("evening", now):
                try:
                    balance = lab_account_balance(client.get_accounts(), str(account.get("accountId")))
                except (CapitalAPIError, ValueError) as exc:
                    _print({"event": "DAYTRADE_BALANCE_UNAVAILABLE", "error": str(exc)})
            edges = {i["lane"].name: dt.edge(dt.closed_r(i["settings"].data_dir / "journal.jsonl"),
                                              i["lane"].prior_r, roster) for i in lanes}
            snapshot = (lab_risk_snapshot(ledgers, account_balance_value=balance, trading_day=now.date(),
                                          max_open_risk=roster.max_account_open_risk,
                                          max_daily_loss=roster.max_account_daily_loss)
                        if balance is not None else None)
            if clock.due("morning", now):
                yesterday = now.astimezone(dt.ZURICH).date() - timedelta(days=1)
                while yesterday.weekday() >= 5:
                    yesterday -= timedelta(days=1)
                say(dt.morning_text(roster, now.astimezone(dt.ZURICH).date(), edges, balance,
                                    dt.day_summary(lane_dirs, yesterday)))
                clock.mark("morning", now)
            if clock.due("evening", now):
                say(dt.evening_text(roster, now.astimezone(dt.ZURICH).date(),
                                    dt.day_summary(lane_dirs, now.astimezone(dt.ZURICH).date()), balance,
                                    bool(snapshot and snapshot.daily_loss_blocked)))
                clock.mark("evening", now)
            for item in lanes:
                lane, runner, executor = item["lane"], item["runner"], item["executor"]
                if reopening[lane.epic] is not None:
                    continue
                e = edges[lane.name]
                if e["paused"] and lane.name not in paused_alerted:
                    write_control(item["settings"].data_dir, paused=True,
                                  changed_by=f"edge sizing (#166): posterior {e['posterior_r']:+.3f}R after {e['n']} trades")
                    say(f"[dt:{lane.name}] ⏸ paused by the pre-registered edge rule: posterior "
                        f"{e['posterior_r']:+.3f}R after {e['n']} trades. Only you can resume it.")
                    paused_alerted.add(lane.name)
                try:
                    _weekend_guard(market_clock, lane.epic, now, runner, executor, item["notifier"],
                                   enabled=item["settings"].flat_before_weekend)
                    _daily_flat_guard(lane.flat_at, now, runner, executor, item["notifier"])
                    budget = None
                    if snapshot is not None:
                        budget = min(snapshot.remaining_open_risk,
                                     balance * roster.risk_per_trade * e["multiplier"])  # type: ignore[operator]
                    limit_block = (
                        "Account balance unavailable" if snapshot is None
                        else f"Portfolio daily loss limit −{roster.max_account_daily_loss:.0%} reached"
                        if snapshot.daily_loss_blocked else None
                    )
                    if item["bracket"] is not None:
                        blocked = runner.entry_block or runner.paused_reason() or limit_block or (
                            "Edge sizing: lane paused" if e["paused"] else None)
                        for event in item["bracket"].sync(
                            runner.strategies[0],
                            lambda lane=lane: client.get_prices(lane.epic, resolution=lane.resolution,
                                                                max_count=lane.price_count)[0][:-1],
                            now, blocked=blocked, max_risk_budget=budget,
                        ):
                            _print({"daytrade_lane": lane.name, **event})
                        runner.entry_block = runner.entry_block or "Entries are placed as stop orders (issue #101)."
                    _manage_positions(executor, runner, item["notifier"], manage=lane.trail, block_adds=True)
                    completed, result = _run_once(client, item["settings"], runner,  # type: ignore[arg-type]
                                                  use_ai=False, paper=True, skip_timestamp=item["last"],
                                                  defer_paper_entry=True)
                    if result is None:
                        continue
                    item["last"] = completed
                    runner.ledger.mark_processed(datetime.fromisoformat(completed))
                    if not result.paper_entry_approved:
                        continue
                    reason = limit_block or ("Edge sizing: lane paused" if e["paused"] else None)
                    if reason or budget is None or budget <= 0:
                        event = {"event": "PORTFOLIO_BLOCK", "timestamp": now.isoformat(),
                                 "reason": reason or "No open-risk room left"}
                        runner.ledger.record_event(event)
                        _print({"daytrade_lane": lane.name, **event})
                        continue
                    execution = executor.execute(result.setup, result.risk, epic=lane.epic, max_risk_budget=budget,
                                                 context=getattr(result, "regime", None))
                    _print({"daytrade_lane": lane.name, "multiplier": e["multiplier"], **execution.to_dict()})
                    if execution.status == "OPENED" and execution.paper_event:
                        _notify_status(item["notifier"], "trade_opened", execution.paper_event,
                                       deal_id=execution.deal_id)
                except (CapitalAPIError, ValueError, RuntimeError) as exc:
                    _print({"daytrade_lane": lane.name, "error": str(exc), "poll": polls})
                    _notify_status(item["notifier"], "error", str(exc))
            if args.iterations == 0 or polls < args.iterations:
                if all(v is not None for v in reopening.values()) and not clock.due("morning", now):
                    first = min(v for v in reopening.values() if v is not None)
                    time.sleep(max(1.0, min(300.0, (first - now).total_seconds())))
                else:
                    time.sleep(roster.poll_seconds)
    return 0


def _lab_command(args: argparse.Namespace, settings: Settings) -> int:
    roster = load_roster(args.roster)
    if args.check:
        _print({"account": roster.account, "entries": [entry.name for entry in roster.entries]})
        return 0
    if not settings.capital_demo_execution_enabled:
        raise RuntimeError("Demo execution is locked. Set CAPITAL_DEMO_EXECUTION_ENABLED=true in .env.")
    telegram: TelegramBotClient | None = None
    if settings.telegram_configured:
        telegram = _telegram_client(settings, require_ids=True)
    try:
        with _capital_client(settings) as raw:
            account = raw.use_account(roster.account)
            if not _account_matches(account, roster.account):
                raise RuntimeError(f"Lab must run on the {roster.account!r} demo account")
            client = PollCache(raw)
            lanes = []
            for entry in roster.entries:
                entry_settings = _lab_settings(settings, roster, entry)
                runner = _runner(entry_settings, use_ai=False)
                executor = _demo_executor(
                    entry_settings, client, runner,  # type: ignore[arg-type]
                    allowed_epics=LAB_EPICS, use_minimum_size=True,
                )
                write_session_info(entry_settings.data_dir, {
                    "session": entry_settings.session, "lab": True, "epic": entry.epic,
                    "account_id": account.get("accountId"), "account_name": account.get("accountName"),
                    "resolution": entry.resolution, "trend_resolution": entry.trend_resolution,
                    "confirm_resolution": entry.confirm_resolution, "strategies": list(entry.strategies),
                    "source": entry.source,
                    "max_account_open_risk": roster.max_account_open_risk,
                    "max_account_daily_loss": roster.max_account_daily_loss,
                    "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                })
                notifier = (
                    TelegramStatusNotifier(
                        telegram, chat_id=str(settings.telegram_chat_id), label=f"lab:{entry.name}",
                        market="Gold" if entry.epic == "GOLD" else entry.epic,
                    )
                    if telegram else None
                )
                lanes.append({"entry": entry, "settings": entry_settings, "runner": runner,
                              "executor": executor, "notifier": notifier,
                              "last": runner.ledger.state.last_processed_timestamp})
            lab_ledgers = [lane["runner"].ledger for lane in lanes]
            _print({"event": "LAB_STARTED", "account": account.get("accountName"),
                    "entries": [entry.name for entry in roster.entries]})
            if telegram and settings.telegram_chat_id:
                try:
                    telegram.send_message(
                        settings.telegram_chat_id,
                        f"🧪 Strategy lab started on demo '{account.get('accountName')}'\n"
                        f"{len(lanes)} strategies: "
                        + ", ".join(lane["entry"].name for lane in lanes)
                        + "\nUse /lab for results.",
                    )
                except TelegramAPIError as exc:
                    _print({"warning": f"Telegram lab startup message failed: {exc}"})
            polls = 0
            market_clock = MarketClock(getattr(raw, "get_market_details", None))
            closed_markets: set[str] = set()
            while args.iterations == 0 or polls < args.iterations:
                polls += 1
                client.new_poll()
                now = datetime.now(timezone.utc)
                reopening = {epic: market_clock.closed_until(epic, now)
                             for epic in {lane["entry"].epic for lane in lanes}}
                for epic, reopens in reopening.items():
                    if reopens is not None and epic not in closed_markets:
                        _print({"event": "MARKET_CLOSED", "epic": epic, "reopens": reopens.isoformat()})
                        closed_markets.add(epic)
                    elif reopens is None and epic in closed_markets:
                        _print({"event": "MARKET_OPEN", "epic": epic})
                        closed_markets.discard(epic)
                account_balance_value: float | None = None
                if any(reopens is None for reopens in reopening.values()):
                    try:
                        account_balance_value = lab_account_balance(
                            client.get_accounts(), str(account.get("accountId"))
                        )
                    except (CapitalAPIError, ValueError) as exc:
                        _print({"event": "LAB_ACCOUNT_RISK_UNAVAILABLE", "error": str(exc)})
                for lane in lanes:
                    entry = lane["entry"]
                    if reopening[entry.epic] is not None:
                        continue  # this market is closed: no API calls for it
                    try:
                        _weekend_guard(
                            market_clock, entry.epic, now, lane["runner"], lane["executor"],
                            lane["notifier"], enabled=lane["settings"].flat_before_weekend,
                        )
                        _daily_flat_guard(entry.flat_at, now, lane["runner"], lane["executor"], lane["notifier"])
                        before_manage = (
                            lab_risk_snapshot(
                                lab_ledgers,
                                account_balance_value=account_balance_value,
                                trading_day=now.date(),
                                max_open_risk=roster.max_account_open_risk,
                                max_daily_loss=roster.max_account_daily_loss,
                            )
                            if entry.manage and account_balance_value is not None
                            else None
                        )
                        _manage_positions(
                            lane["executor"],
                            lane["runner"],
                            lane["notifier"],
                            manage=entry.manage,
                            block_adds=(
                                account_balance_value is None
                                or bool(before_manage and before_manage.daily_loss_blocked)
                            ),
                            max_risk_budget=(
                                before_manage.remaining_open_risk if before_manage else None
                            ),
                        )
                        completed, result = _run_once(
                            client, lane["settings"], lane["runner"],  # type: ignore[arg-type]
                            use_ai=False, paper=True, skip_timestamp=lane["last"],
                            defer_paper_entry=True,
                        )
                        if result is None:
                            continue
                        lane["last"] = completed
                        lane["runner"].ledger.mark_processed(datetime.fromisoformat(completed))
                        if result.paper_entry_approved:
                            if account_balance_value is None:
                                event = {
                                    "event": "ACCOUNT_RISK_BLOCK",
                                    "timestamp": now.isoformat(),
                                    "reason": "Lab account balance is unavailable",
                                }
                                lane["runner"].ledger.record_event(event)
                                _print({"lab_entry": entry.name, **event})
                                continue
                            snapshot = lab_risk_snapshot(
                                lab_ledgers,
                                account_balance_value=account_balance_value,
                                trading_day=now.date(),
                                max_open_risk=roster.max_account_open_risk,
                                max_daily_loss=roster.max_account_daily_loss,
                            )
                            if snapshot.daily_loss_blocked:
                                event = {
                                    "event": "ACCOUNT_DAILY_LOSS_BLOCK",
                                    "timestamp": now.isoformat(),
                                    "reason": "Lab account daily-loss limit has been reached",
                                    **snapshot.to_dict(),
                                }
                                lane["runner"].ledger.record_event(event)
                                _print({"lab_entry": entry.name, **event})
                                continue
                            execution = lane["executor"].execute(
                                result.setup,
                                result.risk,
                                epic=entry.epic,
                                max_risk_budget=snapshot.remaining_open_risk,
                                context=getattr(result, "regime", None),
                            )
                            _print({"lab_entry": entry.name, **execution.to_dict()})
                            if execution.status == "OPENED" and execution.paper_event:
                                _notify_status(
                                    lane["notifier"], "trade_opened", execution.paper_event,
                                    deal_id=execution.deal_id,
                                )
                    except (CapitalAPIError, ValueError, RuntimeError) as exc:
                        _print({"lab_entry": entry.name, "error": str(exc), "poll": polls})
                        _notify_status(lane["notifier"], "error", str(exc))
                if args.iterations == 0 or polls < args.iterations:
                    if all(value is not None for value in reopening.values()):
                        # Every lab market is closed: sleep until the first reopens (at most 5 min).
                        first = min(value for value in reopening.values() if value is not None)
                        time.sleep(max(1.0, min(300.0, (first - now).total_seconds())))
                    else:
                        time.sleep(roster.poll_seconds)
    finally:
        if telegram:
            telegram.close()
    return 0


def _read_csv(path: Path) -> list[Candle]:
    candles: list[Candle] = []
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            candles.append(
                Candle(
                    timestamp=timestamp,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0),
                    spread=float(row.get("spread") or 0),
                )
            )
    return candles


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = _settings(args)
    try:
        if args.command == "search":
            with _capital_client(settings) as client:
                markets = client.search_markets(args.term)
            _print(
                [
                    {
                        "epic": market.get("epic"),
                        "name": market.get("instrumentName"),
                        "status": market.get("marketStatus"),
                    }
                    for market in markets
                ]
            )
            return 0

        if args.command == "backtest":
            return _backtest_command(args, settings)

        if args.command == "stats":
            names = args.sessions or sorted(
                path.name for path in SESSIONS_DIR.glob("*") if path.is_dir()
            )
            if not names:
                raise RuntimeError("No sessions found under data/sessions")
            summaries = [summarize(SESSIONS_DIR / name) for name in names]
            text = "\n\n".join(format_summary(summary) for summary in summaries)
            print(text)
            if args.telegram:
                client = _telegram_client(settings, require_ids=True)
                try:
                    assert settings.telegram_chat_id
                    client.send_message(settings.telegram_chat_id, text)
                finally:
                    client.close()
            return 0

        if args.command == "replay":
            candles = _read_csv(args.file)
            setup = RetestStrategy().analyze(candles)
            _print(setup.to_dict())
            return 0

        if args.command == "demo-check":
            if not settings.capital_epic:
                raise RuntimeError("CAPITAL_EPIC is missing")
            with _capital_client(settings) as client:
                market = client.get_market_details(settings.capital_epic)
                positions = client.get_positions()
            rules = market.get("dealingRules") or {}
            snapshot = market.get("snapshot") or {}
            _print(
                {
                    "base_url": "https://demo-api-capital.backend-capital.com/api/v1",
                    "execution_enabled": settings.capital_demo_execution_enabled,
                    "epic": settings.capital_epic,
                    "market_status": snapshot.get("marketStatus"),
                    "max_demo_size": settings.capital_max_demo_size,
                    "min_deal_size": (rules.get("minDealSize") or {}).get("value"),
                    "size_increment": (rules.get("minSizeIncrement") or {}).get("value"),
                    "open_positions": len(positions),
                }
            )
            return 0

        if args.command == "telegram-discover":
            client = _telegram_client(settings, require_ids=False)
            try:
                updates = client.get_updates(offset=-20, limit=20, timeout=0)
            finally:
                client.close()
            identities: dict[tuple[str, int], dict[str, object]] = {}
            for update in updates:
                message = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
                sender = message.get("from") or (update.get("callback_query") or {}).get("from") or {}
                chat = message.get("chat") or {}
                if chat.get("id") is not None and sender.get("id") is not None:
                    key = (str(chat["id"]), int(sender["id"]))
                    identities[key] = {
                        "chat_id": key[0],
                        "user_id": key[1],
                        "username": sender.get("username"),
                        "chat_type": chat.get("type"),
                    }
            _print(list(identities.values()))
            return 0

        if args.command == "lab":
            return _lab_command(args, settings)

        if args.command == "daytrade":
            return _daytrade_command(args, settings)

        if args.command == "telegram-commands":
            if not settings.telegram_configured:
                raise RuntimeError(
                    "Telegram commands need TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID and "
                    "TELEGRAM_ALLOWED_USER_ID in .env"
                )
            assert settings.telegram_bot_token and settings.telegram_chat_id
            assert settings.telegram_allowed_user_id is not None
            # Long polling waits up to 25 s, so the HTTP timeout must be longer.
            telegram = TelegramBotClient(settings.telegram_bot_token, timeout=40.0)
            service = CommandService(
                telegram,
                chat_id=settings.telegram_chat_id,
                allowed_user_id=settings.telegram_allowed_user_id,
                sessions_dir=SESSIONS_DIR,
                stopped_dir=PROJECT_ROOT.parents[1] / "work" / "goldbot" / "stopped",
                state_path=PROJECT_ROOT / "data" / "telegram_commands_state.json",
                lab_dir=LAB_DIR,
                daytrade_dir=DAYTRADE_DIR,
                logs_dir=PROJECT_ROOT.parents[1] / "work" / "goldbot",
                capital_factory=(
                    (lambda: _capital_client(settings)) if settings.capital_configured else None
                ),
            )
            try:
                run_forever(service, log=_print)
            finally:
                telegram.close()
            return 0

        if args.command == "telegram-test":
            client = _telegram_client(settings, require_ids=True)
            try:
                assert settings.telegram_chat_id
                sent = client.send_message(
                    settings.telegram_chat_id,
                    "Goldbot Telegram connection is working. Paper mode only; no broker order was sent.",
                )
            finally:
                client.close()
            _print({"sent": True, "message_id": sent.get("message_id")})
            return 0

        runner = _runner(settings, use_ai=args.ai)
        if args.demo_execute and not args.paper:
            raise RuntimeError("--demo-execute requires --paper for local risk-state tracking")
        if args.demo_execute and args.command == "watch" and args.telegram:
            raise RuntimeError("--demo-execute cannot be combined with Telegram approval")
        if args.command == "watch" and args.telegram and args.telegram_status:
            raise RuntimeError("Use either --telegram or --telegram-status, not both")
        telegram_client: TelegramBotClient | None = None
        approval_service: TelegramApprovalService | None = None
        status_notifier: TelegramStatusNotifier | None = None
        if args.command == "watch" and (args.telegram or args.telegram_status):
            telegram_client = _telegram_client(settings, require_ids=True)
            if args.telegram:
                if not args.paper:
                    raise RuntimeError("--telegram requires --paper because approvals are paper-only")
                approval_service = _telegram_service(settings, telegram_client, runner)
            else:
                assert settings.telegram_chat_id
                status_notifier = TelegramStatusNotifier(
                    telegram_client,
                    chat_id=settings.telegram_chat_id,
                    label=settings.session,
                    market="Gold" if settings.capital_epic == "GOLD" else str(settings.capital_epic),
                )

        try:
            with _capital_client(settings) as client:
                account: dict[str, object] = {}
                if getattr(args, "account", None):
                    account = client.use_account(args.account)
                    _print(
                        {
                            "account": account.get("accountName"),
                            "account_id": account.get("accountId"),
                            "currency": account.get("currency"),
                        }
                    )
                if args.command == "watch" and settings.session:
                    write_session_info(
                        settings.data_dir,
                        {
                            "session": settings.session,
                            "epic": settings.capital_epic,
                            "account_id": account.get("accountId"),
                            "account_name": account.get("accountName"),
                            "currency": account.get("currency"),
                            "resolution": settings.capital_resolution,
                            "trend_resolution": settings.capital_trend_resolution,
                            "confirm_resolution": settings.capital_confirm_resolution,
                            "demo_execute": bool(args.demo_execute),
                            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        },
                    )
                demo_executor = (
                    _demo_executor(
                        settings, client, runner, allowed_epics=_session_epics(settings, account or getattr(args, "account", None))
                    )
                    if args.demo_execute
                    else None
                )
                if args.command == "once":
                    if demo_executor:
                        _manage_positions(
                            demo_executor, runner, None, manage=settings.manage_trades
                        )
                    _, result = _run_once(
                        client,
                        settings,
                        runner,
                        use_ai=args.ai,
                        paper=args.paper,
                        defer_paper_entry=demo_executor is not None,
                    )
                    assert result is not None
                    _print(result.to_dict())
                    if demo_executor and result.paper_entry_approved:
                        assert settings.capital_epic
                        _print(
                            demo_executor.execute(
                                result.setup,
                                result.risk,
                                epic=settings.capital_epic,
                                context=result.regime,
                            ).to_dict()
                        )
                    return 0

                if args.interval < 10:
                    raise RuntimeError("--interval must be at least 10 seconds")
                _notify_status(
                    status_notifier,
                    "startup",
                    entry_resolution=settings.capital_resolution,
                    trend_resolution=settings.capital_trend_resolution,
                    interval=args.interval,
                    max_trades=settings.max_trades_per_day,
                    strategies=settings.strategies,
                    max_positions=settings.max_positions_per_session,
                    manage=settings.manage_trades,
                )
                path_recorder = (
                    PricePathRecorder(settings.data_dir / "price_paths.jsonl")
                    if settings.record_price_path and demo_executor
                    else None
                )
                bracket = (
                    StopOrderBracket(
                        client, demo_executor, runner.ledger, runner.risk_engine,
                        settings.data_dir / "bracket.json", epic=settings.capital_epic,
                        notifier=status_notifier,
                    )
                    if settings.entry_orders == "stop" and demo_executor and settings.capital_epic
                    else None
                )
                poll_count = 0
                # Persisted, so a restart never re-trades the candle it already acted on.
                last_completed: str | None = runner.ledger.state.last_processed_timestamp
                market_clock = MarketClock(getattr(client, "get_market_details", None))
                market_closed = False
                while args.iterations == 0 or poll_count < args.iterations:
                    poll_count += 1
                    # While the market is closed (daily break, weekend) make no API calls at all.
                    now = datetime.now(timezone.utc)
                    reopens = (
                        market_clock.closed_until(settings.capital_epic, now)
                        if settings.capital_epic else None
                    )
                    if reopens is not None:
                        if not market_closed:
                            _print({"event": "MARKET_CLOSED", "epic": settings.capital_epic,
                                    "reopens": reopens.isoformat()})
                            market_closed = True
                        if args.iterations == 0 or poll_count < args.iterations:
                            time.sleep(max(1.0, min(300.0, (reopens - now).total_seconds())))
                        continue
                    if market_closed:
                        _print({"event": "MARKET_OPEN", "epic": settings.capital_epic})
                        market_closed = False
                    try:
                        if settings.capital_epic:
                            _weekend_guard(
                                market_clock, settings.capital_epic, now, runner, demo_executor,
                                status_notifier, enabled=settings.flat_before_weekend,
                            )
                            _daily_flat_guard(settings.flat_at, now, runner, demo_executor, status_notifier)
                        if bracket is not None:
                            blocked = runner.entry_block or runner.paused_reason()
                            for event in bracket.sync(
                                runner.strategies[0],
                                lambda: client.get_prices(
                                    settings.capital_epic,
                                    resolution=settings.capital_resolution,
                                    max_count=settings.capital_price_count,
                                )[0][:-1],  # completed candles only
                                now,
                                blocked=blocked,
                            ):
                                _print(event)
                            runner.entry_block = runner.entry_block or "Entries are placed as stop orders (issue #101)."
                        if approval_service:
                            for event in approval_service.poll():
                                _print(event)
                        if demo_executor:
                            _manage_positions(
                                demo_executor,
                                runner,
                                status_notifier,
                                manage=settings.manage_trades,
                                recorder=path_recorder,
                            )
                            if path_recorder and settings.capital_epic:
                                _record_price_path(
                                    client, settings.capital_epic, runner.ledger, path_recorder
                                )
                        completed, result = _run_once(
                            client,
                            settings,
                            runner,
                            use_ai=args.ai,
                            paper=args.paper,
                            skip_timestamp=last_completed,
                            defer_paper_entry=(
                                approval_service is not None or demo_executor is not None
                            ),
                        )
                        if result is not None:
                            _print(result.to_dict())
                            last_completed = completed
                            runner.ledger.mark_processed(datetime.fromisoformat(completed))
                            if args.telegram_decisions:
                                _notify_status(status_notifier, "decision", result)
                            for paper_event in result.paper_events:
                                if paper_event.get("event") == "PAPER_CLOSE":
                                    _attach_path(path_recorder, paper_event)
                                    _notify_status(status_notifier, "trade_closed", paper_event)
                                elif paper_event.get("event") == "PAPER_OPEN":
                                    _notify_status(status_notifier, "trade_opened", paper_event)
                            if approval_service:
                                event = approval_service.stage(
                                    result,
                                    spread=result.observed_spread,
                                )
                                if event:
                                    _print(event)
                            if demo_executor and result.paper_entry_approved:
                                assert settings.capital_epic
                                execution_event = demo_executor.execute(
                                    result.setup,
                                    result.risk,
                                    epic=settings.capital_epic,
                                    context=result.regime,
                                )
                                _print(execution_event.to_dict())
                                if execution_event.status == "OPENED" and execution_event.paper_event:
                                    _notify_status(
                                        status_notifier,
                                        "trade_opened",
                                        execution_event.paper_event,
                                        deal_id=execution_event.deal_id,
                                    )
                                elif _worth_notifying(execution_event.status):
                                    _notify_status(
                                        status_notifier,
                                        "execution",
                                        execution_event.to_dict(),
                                    )
                    except (CapitalAPIError, ValueError, RuntimeError) as exc:
                        _print({"error": str(exc), "poll": poll_count})
                        _notify_status(status_notifier, "error", str(exc))
                    if args.iterations == 0 or poll_count < args.iterations:
                        time.sleep(args.interval)
                return 0
        finally:
            if telegram_client:
                telegram_client.close()
    except (CapitalAPIError, OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Stopped.")
        return 130
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
