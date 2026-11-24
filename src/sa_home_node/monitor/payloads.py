from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from sa_home_node.monitor.hostmodels import HostEvent, HostMetricState
from sa_home_node.monitor.models import (
    DispatchResult,
    Event,
    HealthState,
    PowerEvent,
    SmartChange,
)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def event_payload(event: Event) -> dict[str, Any]:
    return {
        "component_id": event.component_id,
        "kind": event.kind,
        "label": event.label,
        "temperature_c": event.temperature_c,
        "at": event.at.isoformat(),
    }


def host_event_payload(event: HostEvent) -> dict[str, Any]:
    return {
        "component_id": event.component_id,
        "metric": event.metric,
        "label": event.label,
        "value": event.value,
        "unit": event.unit,
        "hint": event.hint,
        "at": event.at.isoformat(),
    }


def smart_change_payload(change: SmartChange) -> dict[str, Any]:
    return {
        "component_id": change.component_id,
        "label": change.label,
        "health_from": change.health_from,
        "health_to": change.health_to,
        "attr_changes": [asdict(c) for c in change.attr_changes],
        "at": change.at.isoformat(),
    }


def health_dict(state: HealthState) -> dict[str, Any]:
    return {
        "component_id": state.component_id,
        "kind": state.kind,
        "label": state.label,
        "status": state.status,
        "temperature_c": state.temperature_c,
        "alerting_since": _iso(state.alerting_since),
    }


def host_state_dict(state: HostMetricState) -> dict[str, Any]:
    return {
        "component_id": state.component_id,
        "metric": state.metric,
        "label": state.label,
        "unit": state.unit,
        "status": state.status,
        "value": state.value,
        "alerting_since": _iso(state.alerting_since),
    }


def outage_dict(event: PowerEvent | None) -> dict[str, Any] | None:
    if event is None:
        return None
    downtime = event.downtime
    return {
        "kind": event.kind,
        "boot_at": event.boot_at.isoformat(),
        "down_at": _iso(event.down_at),
        "up_at": _iso(event.up_at),
        "down_approx": event.down_approx,
        "downtime_s": downtime.total_seconds() if downtime is not None else None,
    }


class BroadcastDispatcher:
    def __init__(self, broadcast: Callable[[str, dict], Awaitable[int]]) -> None:
        self._broadcast = broadcast

    async def dispatch_alert(self, event: Event) -> DispatchResult:
        return await self._send(event.type, event_payload(event))

    dispatch_clear = dispatch_alert

    async def dispatch_smart(self, change: SmartChange) -> DispatchResult:
        return await self._send(change.event_type, smart_change_payload(change))

    async def dispatch_host_alert(self, event: HostEvent) -> DispatchResult:
        return await self._send(event.type, host_event_payload(event))

    dispatch_host_clear = dispatch_host_alert

    async def _send(self, event_type: str, data: dict) -> DispatchResult:
        delivered = await self._broadcast(event_type, data) > 0
        return DispatchResult(delivered=delivered, handled=delivered)
