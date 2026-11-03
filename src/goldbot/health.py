"""Mac power and sleep warnings for the Telegram command process (issue #70).

A sleeping Mac sends nothing, so the warnings come before sleep (running on battery, low charge)
and after waking (how long the bots were stopped).
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ZURICH = ZoneInfo("Europe/Zurich")
LEVELS = (50, 20, 10, 5)


def read_power() -> tuple[str, int] | None:
    """("AC" | "Battery", percent) from `pmset -g batt`, or None when unavailable."""
    try:
        output = subprocess.run(
            ["/usr/bin/pmset", "-g", "batt"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    percent = re.search(r"(\d+)%", output)
    if "Battery Power" in output:
        source = "Battery"
    elif "AC Power" in output:
        source = "AC"
    else:
        return None
    return source, int(percent.group(1)) if percent else -1


def _zurich(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).astimezone(ZURICH).strftime("%a %d %b %H:%M")


class PowerMonitor:
    def __init__(
        self,
        *,
        read: Callable[[], tuple[str, int] | None] = read_power,
        clock: Callable[[], float] = time.time,
        gap_seconds: float = 600,
    ) -> None:
        self.read = read
        self.clock = clock
        self.gap_seconds = gap_seconds
        self.last_tick: float | None = None
        self.last_source: str | None = None
        self.alerted: set[int] = set()

    def check(self) -> list[str]:
        messages = []
        now = self.clock()
        if self.last_tick is not None and now - self.last_tick > self.gap_seconds:
            minutes = int((now - self.last_tick) // 60)
            messages.append(
                f"😴 The Mac was asleep or paused for {minutes // 60} h {minutes % 60} min "
                f"({_zurich(self.last_tick)} → {_zurich(now)}). The bots did not trade then; "
                "they have resumed."
            )
        self.last_tick = now
        power = self.read()
        if power is None:
            return messages
        source, percent = power
        if source == "Battery":
            if self.last_source != "Battery":
                messages.append(
                    f"🔋 The Mac is running on battery ({percent}%). The bots stop when it goes "
                    "to sleep. Please plug it in."
                )
                # Levels already passed need no separate warning.
                self.alerted = {level for level in LEVELS if percent <= level}
            else:
                crossed = [level for level in LEVELS if percent <= level and level not in self.alerted]
                if crossed:
                    self.alerted.update(crossed)
                    messages.append(
                        f"🪫 Battery at {percent}%. The Mac will sleep soon and the bots stop."
                    )
        elif self.last_source == "Battery":
            messages.append(f"🔌 The Mac is back on power ({percent}%).")
            self.alerted = set()
        self.last_source = source
        return messages
