"""Per-session control and metadata files shared by the bots and the Telegram command process.

- `control.json` is written by the command process (/pause, /resume) and read by the bot on
  every poll. A paused bot takes no new entries or adds; stops, trailing and closes continue.
- `session.json` is written by the bot at startup so the command process knows which
  Capital.com account and timeframes the session uses.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONTROL_FILE = "control.json"
SESSION_FILE = "session.json"


@dataclass(frozen=True)
class SessionControl:
    paused: bool = False
    changed_at: str | None = None
    changed_by: str | None = None


def read_control(session_dir: Path) -> SessionControl:
    try:
        payload = json.loads((session_dir / CONTROL_FILE).read_text(encoding="utf-8"))
        return SessionControl(
            paused=bool(payload.get("paused", False)),
            changed_at=payload.get("changed_at"),
            changed_by=payload.get("changed_by"),
        )
    except (OSError, ValueError, AttributeError):
        return SessionControl()


def write_control(session_dir: Path, *, paused: bool, changed_by: str) -> SessionControl:
    control = SessionControl(
        paused=paused,
        changed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        changed_by=changed_by,
    )
    _write_json(session_dir / CONTROL_FILE, asdict(control))
    return control


def write_session_info(session_dir: Path, info: dict[str, Any]) -> None:
    _write_json(session_dir / SESSION_FILE, info)


def read_session_info(session_dir: Path) -> dict[str, Any]:
    try:
        payload = json.loads((session_dir / SESSION_FILE).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
