from __future__ import annotations

from goldbot.health import PowerMonitor


class Fake:
    def __init__(self) -> None:
        self.now = 1_790_000_000.0
        self.power: tuple[str, int] | None = ("AC", 100)

    def clock(self) -> float:
        return self.now

    def read(self):
        return self.power


def test_warns_on_battery_at_levels_and_when_power_returns() -> None:
    fake = Fake()
    monitor = PowerMonitor(read=fake.read, clock=fake.clock)
    assert monitor.check() == []
    fake.power = ("Battery", 64)
    assert "running on battery (64%)" in monitor.check()[0]
    fake.power = ("Battery", 60)
    assert monitor.check() == []
    fake.power = ("Battery", 49)
    assert "Battery at 49%" in monitor.check()[0]
    fake.power = ("Battery", 48)
    assert monitor.check() == []
    fake.power = ("Battery", 9)  # skipped 20%: one warning, not two
    assert len(monitor.check()) == 1
    fake.power = ("AC", 10)
    assert "back on power" in monitor.check()[0]


def test_starting_on_low_battery_gives_one_warning() -> None:
    fake = Fake()
    fake.power = ("Battery", 15)
    monitor = PowerMonitor(read=fake.read, clock=fake.clock)
    assert len(monitor.check()) == 1
    fake.power = ("Battery", 14)
    assert monitor.check() == []
    fake.power = ("Battery", 10)
    assert "Battery at 10%" in monitor.check()[0]


def test_reports_how_long_the_mac_slept_in_zurich_time() -> None:
    fake = Fake()
    monitor = PowerMonitor(read=fake.read, clock=fake.clock)
    monitor.check()
    fake.now += 30
    assert monitor.check() == []
    fake.now += 45 * 3600 + 40 * 60
    message = monitor.check()[0]
    assert "asleep or paused for 45 h 40 min" in message


def test_unreadable_power_is_ignored() -> None:
    fake = Fake()
    fake.power = None
    assert PowerMonitor(read=fake.read, clock=fake.clock).check() == []
