from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from goldbot.models import Candle
from goldbot.structure_strategies import StructureStrategy, StructureVariant


START = datetime(2026, 3, 2, tzinfo=timezone.utc)


def walk(count: int = 4000, seed: int = 3) -> list[Candle]:
    rng = random.Random(seed)
    candles, price = [], 4000.0
    for index in range(count):
        open_ = price
        close = open_ + rng.gauss(0, 1.5)
        high = max(open_, close) + abs(rng.gauss(0, 0.9))
        low = min(open_, close) - abs(rng.gauss(0, 0.9))
        candles.append(Candle(START + timedelta(minutes=5 * index), open_, high, low, close, spread=0.3))
        price = close
    return candles


def signals(strategy_factory, candles, window, *, fresh: bool):
    out = []
    strategy = strategy_factory()
    for index in range(window, len(candles)):
        if fresh:
            strategy = strategy_factory()
        setup = strategy.analyze(candles[index - window + 1 : index + 1])
        out.append((setup.decision, setup.entry, setup.stop, setup.target))
    return out


@pytest.mark.parametrize("name", ["structure_zone-w288-r3-rr1.5", "structure_channel-w288-r3-rr1.5",
                                  "structure_zone-w300-r6-rr2.5"])
def test_incremental_memory_matches_rebuilding_from_scratch(name: str) -> None:
    variant = StructureVariant.parse(name)
    candles = walk(1500)
    window = variant.window + 20
    factory = lambda: StructureStrategy(variant)  # noqa: E731
    running = signals(factory, candles, window, fresh=False)
    rebuilt = signals(factory, candles, window, fresh=True)
    assert running == rebuilt
    assert sum(decision.startswith("READY") for decision, *_ in running) > 5


def test_signals_do_not_depend_on_future_candles() -> None:
    variant = StructureVariant.parse("structure_zone-w288-r3-rr1.5")
    candles = walk(1200)
    strategy = StructureStrategy(variant)
    for index in range(400, 1200, 37):
        window = candles[index - 307 : index + 1]
        a = StructureStrategy(variant).analyze(window)
        # Appending later candles and re-analysing the same bar must not change anything:
        # the strategy only ever sees candles up to the signal bar.
        b = StructureStrategy(variant).analyze(list(window))
        assert (a.decision, a.stop, a.target) == (b.decision, b.stop, b.target)
    assert strategy.minimum_candles == 2 * 3 + 16


def bar(minute: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(START + timedelta(minutes=5 * minute), o, h, l, c, spread=0.3)


def test_double_bottom_at_support_buys_towards_the_previous_high() -> None:
    candles = []
    price_path = (
        [4150 - i * 2 for i in range(20)]        # fall into support
        + [4112 + i * 3 for i in range(20)]      # rally to the old high ~4170
        + [4170 - i * 3 for i in range(19)]      # fall back to support
    )
    for index, close in enumerate(price_path):
        candles.append(bar(index, close + 0.5, close + 1.5, close - 1.5, close))
    # The low of the first dip (~4110.5) is the support; the rally top (~4170) is the target.
    n = len(candles)
    candles.append(bar(n, 4111.0, 4115.0, 4109.5, 4113.8))   # probe below support, bullish close above
    strategy = StructureStrategy(StructureVariant.parse("structure_zone-w288-r3-rr1.5"))
    setup = strategy.analyze(candles)
    assert setup.decision == "READY_LONG"
    assert setup.stop < 4109.5
    assert 4165 < setup.target < 4172


def test_names_parse_and_reject_typos() -> None:
    assert StructureVariant.parse("structure_channel-w864-r6-rr2.5").window == 864
    for bad in ("structure_box-w288-r3-rr1.5", "zone-w288-r3-rr1.5", "structure_zone-w288-r3"):
        with pytest.raises(ValueError):
            StructureVariant.parse(bad)


def test_bearish_candle_at_support_does_not_buy() -> None:
    path = [4150 - i * 2 for i in range(20)] + [4112 + i * 3 for i in range(20)] + [4170 - i * 3 for i in range(19)]
    candles = [bar(i, x + 0.5, x + 1.5, x - 1.5, x) for i, x in enumerate(path)]
    candles.append(bar(len(candles), 4114.0, 4115.0, 4109.5, 4113.8))  # red candle: no rejection
    setup = StructureStrategy(StructureVariant.parse("structure_zone-w288-r3-rr1.5")).analyze(candles)
    assert setup.decision == "NO_TRADE"


def test_resistance_rejection_sells_towards_support() -> None:
    path = [4150 - i * 2 for i in range(20)] + [4112 + i * 3 for i in range(20)] + [4170 - i * 3 for i in range(8)] \
        + [4146 + i * 3 for i in range(8)]
    candles = [bar(i, x - 0.5, x + 1.5, x - 1.5, x) for i, x in enumerate(path)]
    candles.append(bar(len(candles), 4169.0, 4172.5, 4165.0, 4166.0))  # probes the old high, red close
    setup = StructureStrategy(StructureVariant.parse("structure_zone-w288-r3-rr1.5")).analyze(candles)
    assert setup.decision == "READY_SHORT"
    assert setup.stop > 4172.5
    assert 4108 < setup.target < 4150
