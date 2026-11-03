from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))
import structure_study as study  # noqa: E402

from goldbot.models import Candle  # noqa: E402


def test_each_timeframe_has_32_unique_variants() -> None:
    for timeframe in ("5m", "1m"):
        grid = study.variants(timeframe)
        assert len(grid) == 32 and len({v.id for v in grid}) == 32
    assert study.variants("5m")[0].id == "5m-structure_zone-w288-r3-rr1.5-none"
    assert {v.window for v in study.variants("1m")} == {1440, 4320}


def test_warm_up_is_exactly_the_lookback_so_no_signal_precedes_the_period() -> None:
    start = datetime(2025, 10, 1, tzinfo=timezone.utc)
    bars = [Candle(start + timedelta(minutes=i - 2000), 1, 2, 0.5, 1.5, spread=0.3) for i in range(4000)]
    study.STATE.update(start=start, end=start + timedelta(days=30), entry=bars)
    variant = study.Variant("1m", "zone", 1440, 5, 1.5, None)

    window = study.window_for(variant)

    assert len([b for b in window if b.timestamp < start]) == variant.window + 50
    assert window[variant.window + 50].timestamp == start


def test_month_buckets_cover_the_half_year() -> None:
    study.STATE.update(timeframe="1m", start=datetime(2025, 10, 1, tzinfo=timezone.utc),
                       end=datetime(2026, 4, 1, tzinfo=timezone.utc))
    assert len(study.buckets([])) == 6
    study.STATE.update(timeframe="5m", start=datetime(2024, 9, 29, tzinfo=timezone.utc),
                       end=datetime(2025, 9, 29, tzinfo=timezone.utc))
    assert len(study.buckets([])) == 4
