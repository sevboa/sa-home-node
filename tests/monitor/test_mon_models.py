from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sa_home_node.monitor import models

T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

CONSTANTS = {
    "OK": "ok",
    "ALERTING": "alerting",
    "KIND_CPU": "cpu",
    "KIND_GPU": "gpu",
    "KIND_DISK": "disk",
    "EVENT_OVERHEAT_STARTED": "overheat_started",
    "EVENT_OVERHEAT_CLEARED": "overheat_cleared",
    "EVENT_SMART_DEGRADED": "smart_degraded",
    "EVENT_SMART_RECOVERED": "smart_recovered",
    "EVENT_SYSTEM": "system",
    "POWER_CLEAN": "clean",
    "POWER_UNEXPECTED": "unexpected",
    "DISK_OK": "ok",
    "DISK_WARN": "warning",
    "DISK_FAIL": "failed",
    "BAND_OVER": "over",
    "BAND_UNDER": "under",
    "BAND_MID": "mid",
}


@pytest.mark.parametrize(("name", "value"), CONSTANTS.items())
def test_constant_values(name, value):
    assert getattr(models, name) == value


@pytest.mark.parametrize(
    ("down", "up", "expected"),
    [
        (T0, T0 + timedelta(minutes=5), timedelta(minutes=5)),
        (None, T0, None),
        (T0, None, None),
    ],
)
def test_power_event_downtime(down, up, expected):
    event = models.PowerEvent(models.POWER_CLEAN, T0, down, up)
    assert event.downtime == expected
    assert event.down_approx is False


def test_dataclass_defaults_and_policy():
    disk = models.DiskSummary("HDD1", None, None, None, None)
    assert disk.model is None
    assert disk.kind == "hdd"
    assert models.DispatchResult(True, False) == models.DispatchResult(delivered=True, handled=False)

    class Fixed:
        def band(self, reading):
            return models.BAND_OVER

    policy = models.ComponentPolicy(Fixed(), 3, 2)
    reading = models.SensorReading("cpu:package", models.KIND_CPU, "CPU", 90.0, T0)
    assert policy.policy.band(reading) == models.BAND_OVER
    assert models.BaselineStats(5, 40.0, 2.0).count == 5


def test_remaining_types_importable():
    for name in ("KnownState", "HealthState", "Transition", "HealthDiff", "Event", "SmartSnapshot", "SmartAttrChange", "SmartChange"):
        assert hasattr(models, name)
