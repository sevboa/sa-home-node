from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from sa_home_node.monitor.models import BAND_MID, BAND_OVER, BAND_UNDER

EVENT_HOST_DEGRADED = "host_degraded"
EVENT_HOST_RECOVERED = "host_recovered"

DIR_ABOVE = "above"
DIR_BELOW = "below"

INSTANCE_LABEL_MOUNT = "mount"
INSTANCE_LABEL_IFACE = "iface"


@dataclass(frozen=True)
class HostMetricSpec:
    metric: str
    label: str
    unit: str
    direction: str
    warn: float | None
    crit: float | None
    hysteresis: float
    hint: str
    instance_label: str | None = None

    def band(self, value: float) -> str:
        if self.warn is None:
            return BAND_MID
        if self.direction == DIR_ABOVE:
            if value >= self.warn:
                return BAND_OVER
            if value <= self.warn - self.hysteresis:
                return BAND_UNDER
            return BAND_MID
        if value <= self.warn:
            return BAND_OVER
        if value >= self.warn + self.hysteresis:
            return BAND_UNDER
        return BAND_MID

    def with_thresholds(self, warn: float, crit: float) -> HostMetricSpec:
        return replace(self, warn=warn, crit=crit)

    def without_thresholds(self) -> HostMetricSpec:
        return replace(self, warn=None, crit=None)


def fmt_host_value(value: float, unit: str) -> str:
    if unit == "%":
        return f"{value:.0f}%"
    if unit == "":
        return f"{value:.1f}"
    return f"{value:.0f}{unit}"


@dataclass(frozen=True)
class HostMetricReading:
    component_id: str
    metric: str
    label: str
    value: float
    unit: str
    taken_at: datetime


@dataclass(frozen=True)
class HostMetricState:
    component_id: str
    metric: str
    label: str
    value: float
    unit: str
    status: str
    consecutive_count: int
    alerting_since: datetime | None


@dataclass(frozen=True)
class HostTransition:
    component_id: str
    metric: str
    label: str
    value: float
    unit: str
    hint: str
    from_status: str
    to_status: str
    at: datetime


@dataclass(frozen=True)
class HostHealthDiff:
    states: list[HostMetricState]
    transitions: list[HostTransition]


@dataclass(frozen=True)
class HostEvent:
    type: str
    component_id: str
    metric: str
    label: str
    value: float
    unit: str
    hint: str
    at: datetime


@dataclass(frozen=True)
class HostMetricPolicy:
    spec: HostMetricSpec
    consecutive_to_alert: int
    consecutive_to_clear: int


HostPolicyResolver = Callable[[HostMetricReading], HostMetricPolicy]
