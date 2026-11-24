from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sa_home_node.monitor import payloads
from sa_home_node.monitor.hostmodels import HostEvent, HostMetricState
from sa_home_node.monitor.models import (
    DispatchResult,
    Event,
    HealthState,
    PowerEvent,
    SmartAttrChange,
    SmartChange,
)

T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
T0_ISO = "2026-01-01T10:00:00+00:00"

EVENT = Event("overheat_started", "cpu:package", "cpu", "CPU", 91.5, T0)
HOST_EVENT = HostEvent("host_degraded", "host:iowait", "iowait", "IO wait", 42.0, "%", "hint", T0)
SMART = SmartChange(
    "disk:/dev/sda",
    "Disk-A",
    "smart_degraded",
    "ok",
    "failed",
    (SmartAttrChange(5, "Reallocated", 1, 3),),
    T0,
)


def test_event_payload():
    assert payloads.event_payload(EVENT) == {
        "component_id": "cpu:package",
        "kind": "cpu",
        "label": "CPU",
        "temperature_c": 91.5,
        "at": T0_ISO,
    }


def test_host_event_payload():
    assert payloads.host_event_payload(HOST_EVENT) == {
        "component_id": "host:iowait",
        "metric": "iowait",
        "label": "IO wait",
        "value": 42.0,
        "unit": "%",
        "hint": "hint",
        "at": T0_ISO,
    }


def test_smart_change_payload():
    assert payloads.smart_change_payload(SMART) == {
        "component_id": "disk:/dev/sda",
        "label": "Disk-A",
        "health_from": "ok",
        "health_to": "failed",
        "attr_changes": [{"attr_id": 5, "name": "Reallocated", "old": 1, "new": 3}],
        "at": T0_ISO,
    }


@pytest.mark.parametrize("since,expected", [(None, None), (T0, T0_ISO)])
def test_state_dicts(since, expected):
    health = HealthState("cpu:package", "cpu", "CPU", "alerting", 80.0, 3, since)
    host = HostMetricState("host:iowait", "iowait", "IO wait", 5.0, "%", "ok", 0, since)
    assert payloads.health_dict(health) == {
        "component_id": "cpu:package",
        "kind": "cpu",
        "label": "CPU",
        "status": "alerting",
        "temperature_c": 80.0,
        "alerting_since": expected,
    }
    assert payloads.host_state_dict(host) == {
        "component_id": "host:iowait",
        "metric": "iowait",
        "label": "IO wait",
        "unit": "%",
        "status": "ok",
        "value": 5.0,
        "alerting_since": expected,
    }


def test_outage_dict():
    assert payloads.outage_dict(None) is None
    full = PowerEvent("unexpected", T0, T0 - timedelta(minutes=5), T0 - timedelta(minutes=2), True)
    assert payloads.outage_dict(full) == {
        "kind": "unexpected",
        "boot_at": T0_ISO,
        "down_at": "2026-01-01T09:55:00+00:00",
        "up_at": "2026-01-01T09:58:00+00:00",
        "down_approx": True,
        "downtime_s": 180.0,
    }
    open_ended = payloads.outage_dict(PowerEvent("clean", T0, None, None))
    assert open_ended["down_at"] is None
    assert open_ended["up_at"] is None
    assert open_ended["downtime_s"] is None


@pytest.mark.parametrize("count,ok", [(0, False), (2, True)])
async def test_dispatcher(count, ok):
    sent = []

    async def broadcast(name, data):
        sent.append((name, data))
        return count

    dispatcher = payloads.BroadcastDispatcher(broadcast)
    results = [
        await dispatcher.dispatch_alert(EVENT),
        await dispatcher.dispatch_clear(EVENT),
        await dispatcher.dispatch_smart(SMART),
        await dispatcher.dispatch_host_alert(HOST_EVENT),
        await dispatcher.dispatch_host_clear(HOST_EVENT),
    ]
    assert results == [DispatchResult(delivered=ok, handled=ok)] * 5
    assert [n for n, _ in sent] == [
        "overheat_started",
        "overheat_started",
        "smart_degraded",
        "host_degraded",
        "host_degraded",
    ]
    assert sent[0][1] == payloads.event_payload(EVENT)
    assert sent[2][1] == payloads.smart_change_payload(SMART)
