from __future__ import annotations

from datetime import datetime
from typing import Any

from sa_home_node.monitor.hostmodels import HostEvent, HostMetricState
from sa_home_node.monitor.models import (
    DiskSummary,
    Event,
    HealthState,
    PowerEvent,
    SmartAttrChange,
    SmartChange,
)

Raw = dict[str, Any]


def _iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def parse_health_state(raw: Raw) -> HealthState:
    return HealthState(
        component_id=raw["component_id"],
        kind=raw["kind"],
        label=raw["label"],
        status=raw["status"],
        temperature_c=raw["temperature_c"],
        consecutive_count=0,
        alerting_since=_iso(raw.get("alerting_since")),
    )


def parse_host_metric_state(raw: Raw) -> HostMetricState:
    return HostMetricState(
        component_id=raw["component_id"],
        metric=raw["metric"],
        label=raw["label"],
        value=raw["value"],
        unit=raw.get("unit") or "",
        status=raw["status"],
        consecutive_count=0,
        alerting_since=_iso(raw.get("alerting_since")),
    )


def parse_disk_summary(raw: Raw) -> DiskSummary:
    label = raw["label"]
    return DiskSummary(
        label=label,
        health=raw.get("health"),
        temperature_c=raw.get("temperature_c"),
        free_bytes=raw.get("free_bytes"),
        total_bytes=raw.get("total_bytes"),
        model=raw.get("model"),
        kind=raw.get("kind") or ("emmc" if label == "eMMC" else "hdd"),
    )


def parse_outage(raw: Raw | None) -> PowerEvent | None:
    if raw is None:
        return None
    return PowerEvent(
        kind=raw["kind"],
        boot_at=datetime.fromisoformat(raw["boot_at"]),
        down_at=_iso(raw.get("down_at")),
        up_at=_iso(raw.get("up_at")),
        down_approx=bool(raw.get("down_approx", False)),
    )


def parse_overheat_event(event_type: str, data: Raw) -> Event:
    return Event(
        type=event_type,
        component_id=data["component_id"],
        kind=data["kind"],
        label=data["label"],
        temperature_c=data["temperature_c"],
        at=_iso(data["at"]),
    )


def parse_host_event(event_type: str, data: Raw) -> HostEvent:
    return HostEvent(
        type=event_type,
        component_id=data["component_id"],
        metric=data["metric"],
        label=data["label"],
        value=data["value"],
        unit=data.get("unit") or "",
        hint=data.get("hint") or "",
        at=_iso(data["at"]),
    )


def parse_smart_change(event_type: str, data: Raw) -> SmartChange:
    return SmartChange(
        component_id=data["component_id"],
        label=data["label"],
        event_type=event_type,
        health_from=data.get("health_from"),
        health_to=data.get("health_to"),
        attr_changes=tuple(
            SmartAttrChange(item["attr_id"], item["name"], item["old"], item["new"])
            for item in data.get("attr_changes") or ()
        ),
        at=_iso(data["at"]),
    )
