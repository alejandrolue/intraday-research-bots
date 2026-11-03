from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def summarize(session_dir: Path) -> dict[str, Any]:
    """Summarize closed trades from a session's journal and its current state."""
    closes: list[dict[str, Any]] = []
    fees: dict[str, float] = defaultdict(float)
    journal = session_dir / "journal.jsonl"
    if journal.exists():
        with journal.open(encoding="utf-8") as stream:
            for line in stream:
                event = json.loads(line)
                if event.get("event") == "PAPER_CLOSE":
                    closes.append(event)
                elif event.get("event") == "FINANCING" and event.get("currency"):
                    fees[str(event["currency"]).upper()] += float(event.get("amount") or 0.0)

    state: dict[str, Any] = {}
    state_path = session_dir / "paper_state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))

    by_strategy: dict[str, list[float]] = defaultdict(list)
    broker_by_currency: dict[str, list[float]] = defaultdict(list)
    broker_by_strategy: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    r_multiples: list[float] = []
    for event in closes:
        position = event["position"]
        strategy = position.get("strategy", "unspecified")
        by_strategy[strategy].append(float(event["pnl"]))
        if event.get("broker_pnl") is not None and event.get("broker_currency"):
            currency = str(event["broker_currency"]).upper()
            broker_pnl = float(event["broker_pnl"])
            broker_by_currency[currency].append(broker_pnl)
            broker_by_strategy[strategy][currency].append(broker_pnl)
        initial_stop = position.get("initial_stop") or position["stop"]
        risk = abs(position["entry"] - initial_stop) * position["units"]
        if risk > 0:
            r_multiples.append(float(event["pnl"]) / risk)

    pnls = [float(event["pnl"]) for event in closes]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    return {
        "session": session_dir.name,
        "trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(pnls) if pnls else None,
        "net_pnl": sum(pnls),
        "average_r": sum(r_multiples) / len(r_multiples) if r_multiples else None,
        "profit_factor": sum(wins) / -sum(losses) if losses else None,
        "balance": state.get("balance"),
        "open_positions": state.get("open_positions")
        or ([state["open_position"]] if state.get("open_position") else []),
        "adds": sum(1 for event in closes if event["position"].get("role") == "add"),
        "financing": dict(sorted(fees.items())),
        "broker_pnl": {
            currency: {
                "trades": len(values),
                "wins": sum(value > 0 for value in values),
                "losses": sum(value < 0 for value in values),
                "net_pnl": sum(values),
            }
            for currency, values in sorted(broker_by_currency.items())
        },
        "by_strategy": {
            name: {
                "trades": len(values),
                "net_pnl": sum(values),
                "broker_pnl": {
                    currency: {"trades": len(amounts), "net_pnl": sum(amounts)}
                    for currency, amounts in sorted(broker_by_strategy[name].items())
                },
            }
            for name, values in sorted(by_strategy.items())
        },
    }


def format_summary(summary: dict[str, Any]) -> str:
    lines = [f"📊 {summary['session']}: {summary['trades']} closed trades"]
    if summary["trades"]:
        lines.append(
            f"Local W/L: {summary['wins']}/{summary['losses']} "
            f"({summary['win_rate']:.0%} win rate)"
        )
        matched = sum(values["trades"] for values in summary["broker_pnl"].values())
        if summary["broker_pnl"]:
            for currency, values in summary["broker_pnl"].items():
                lines.append(
                    f"Broker realised: {currency} {values['net_pnl']:+.2f} "
                    f"(W/L {values['wins']}/{values['losses']}, "
                    f"{values['trades']}/{summary['trades']} trades matched)"
                )
        else:
            lines.append(f"Broker realised: unavailable ({matched}/{summary['trades']} matched)")
        for currency, amount in summary.get("financing", {}).items():
            broker = summary["broker_pnl"].get(currency, {}).get("net_pnl")
            lines.append(
                f"Overnight fees: {currency} {amount:+.2f}"
                + (f" (net after fees {currency} {broker + amount:+.2f})" if broker is not None else "")
            )
        lines.append(f"Local estimate: USD {summary['net_pnl']:+.2f}")
        if summary["average_r"] is not None:
            lines.append(f"Average (local): {summary['average_r']:+.2f}R per trade")
        if summary["profit_factor"] is not None:
            lines.append(f"Profit factor (local): {summary['profit_factor']:.2f}")
        for name, values in summary["by_strategy"].items():
            broker = ", ".join(
                f"broker {currency} {amounts['net_pnl']:+.2f} ({amounts['trades']} matched)"
                for currency, amounts in values["broker_pnl"].items()
            )
            lines.append(
                f"  {name}: {values['trades']} trades, local USD {values['net_pnl']:+.2f}"
                + (f", {broker}" if broker else "")
            )
    if summary["balance"] is not None:
        lines.append(f"Local balance estimate: USD {summary['balance']:,.2f}")
    if summary["adds"]:
        lines.append(f"Closed trades that were adds to winners: {summary['adds']}")
    for position in summary["open_positions"]:
        lines.append(
            f"Open: {position['direction']} @ {position['entry']:.2f} "
            f"(stop {position['stop']:.2f}, target {position['target']:.2f})"
        )
    return "\n".join(lines)
