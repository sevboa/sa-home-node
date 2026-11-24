from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest

from sa_home_node.monitor.hostmodels import (
    DIR_ABOVE,
    DIR_BELOW,
    HostMetricReading,
    HostMetricSpec,
    fmt_host_value,
)
from sa_home_node.monitor.models import BAND_MID, BAND_OVER, BAND_UNDER


def spec(direction: str) -> HostMetricSpec:
    return HostMetricSpec("m", "Метрика", "%", direction, 10.0, 25.0, 3.0, "подсказка")


@pytest.mark.parametrize(
    ("direction", "value", "expected"),
    [
        (DIR_ABOVE, 10.0, BAND_OVER),
        (DIR_ABOVE, 50.0, BAND_OVER),
        (DIR_ABOVE, 9.9, BAND_MID),
        (DIR_ABOVE, 7.1, BAND_MID),
        (DIR_ABOVE, 7.0, BAND_UNDER),
        (DIR_ABOVE, 0.0, BAND_UNDER),
        (DIR_BELOW, 10.0, BAND_OVER),
        (DIR_BELOW, 1.0, BAND_OVER),
        (DIR_BELOW, 10.1, BAND_MID),
        (DIR_BELOW, 12.9, BAND_MID),
        (DIR_BELOW, 13.0, BAND_UNDER),
        (DIR_BELOW, 90.0, BAND_UNDER),
    ],
)
def test_band(direction: str, value: float, expected: str) -> None:
    assert spec(direction).band(value) == expected


def test_with_thresholds_keeps_rest() -> None:
    base = spec(DIR_BELOW)
    changed = base.with_thresholds(5.0, 2.0)
    assert (changed.warn, changed.crit) == (5.0, 2.0)
    assert dataclasses.replace(changed, warn=base.warn, crit=base.crit) == base


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [(12.6, "%", "13%"), (3.14159, "", "3.1"), (1234.4, "ms", "1234ms"), (0.0, "%", "0%")],
)
def test_fmt_host_value(value: float, unit: str, expected: str) -> None:
    assert fmt_host_value(value, unit) == expected


def test_reading_is_frozen() -> None:
    reading = HostMetricReading("host:m", "m", "Метрика", 1.0, "%", datetime(2026, 1, 1))
    with pytest.raises(dataclasses.FrozenInstanceError):
        reading.value = 2.0  # type: ignore[misc]
