"""Issue #69: trade the opposite of a strategy's signal.

- mirror: opposite direction, same stop distance on the other side, same reward ratio;
- exact: opposite direction, stop at the original target and target at the original stop
  (the exact opposite trade, usually risking more than it can win);
- trend-keep: mirror geometry, but inverted before the higher-timeframe filter, so only reversed
  trades that agree with the trend are taken (mirror and exact invert after the filter).

The original setup's reward-to-risk is checked before inverting, so every mode starts from the
same signals as the original strategy.
"""

from __future__ import annotations

from dataclasses import replace

from .models import MarketSetup

INVERT_MODES = ("mirror", "exact", "trend-keep")


def original_reward_ok(setup: MarketSetup, minimum: float) -> bool:
    assert setup.entry is not None and setup.stop is not None and setup.target is not None
    risk = abs(setup.entry - setup.stop)
    return risk > 0 and abs(setup.target - setup.entry) / risk >= minimum - 1e-9


def invert_setup(setup: MarketSetup, mode: str) -> MarketSetup:
    if mode not in INVERT_MODES:
        raise ValueError(f"Unknown invert mode {mode!r}")
    if not setup.ready:
        return setup
    assert setup.entry is not None and setup.stop is not None and setup.target is not None
    entry, stop, target = setup.entry, setup.stop, setup.target
    if mode == "exact":
        new_stop, new_target = target, stop
    else:
        new_stop, new_target = entry + (entry - stop), entry - (target - entry)
    long = setup.decision == "READY_SHORT"
    return replace(
        setup,
        decision="READY_LONG" if long else "READY_SHORT",
        bias="BULLISH" if long else "BEARISH",
        stop=new_stop,
        target=new_target,
        strategy=f"{setup.strategy}-inv-{mode}",
        reason=f"Inverted ({mode}): {setup.reason}",
    )
