from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sa_home_node.monitor.config import MetricsConfig, SensorsConfig
from sa_home_node.monitor.db import Database, apply_schema
from sa_home_node.monitor.hostmodels import HostMetricReading
from sa_home_node.monitor.jobbase import JobContext
from sa_home_node.monitor.models import DispatchResult
from sa_home_node.monitor.scanhost import host_resolver, scan_host
from sa_home_node.monitor.sensors.hostscan import KERNEL_SCAN_STATE_KEY
from sa_home_node.monitor.store import MonitorStore

FIXTURE = Path(__file__).parents[1] / "fixtures" / "monitor" / "service" / "jobs.json"
SCENARIOS = json.loads(FIXTURE.read_text(encoding="utf-8"))["sensor_scan"]
BASE = datetime(2026, 3, 1, tzinfo=UTC)
STATE_COLUMNS = (
    "component_id",
    "metric",
    "label",
    "unit",
    "status",
    "last_value",
    "consecutive_count",
    "alerting_since",
    "first_seen_at",
    "last_seen_at",
    "notified_alert_at",
    "notified_cleared_at",
)


class FakeSensors:
    def __init__(self):
        self.readings: list[HostMetricReading] = []
        self.calls: list[dict] = []

    async def read_host(self, kernel_since=None, export=False):
        self.calls.append({"kernel_since": kernel_since, "export": export})
        return self.readings


class FakeDispatcher:
    def __init__(self):
        self.handled = True
        self.delivered = True
        self.calls: list[dict] = []

    async def _dispatch(self, method, event):
        payload = {**asdict(event), "at": event.at.isoformat()}
        self.calls.append({"method": method, "payload": payload})
        return DispatchResult(delivered=self.delivered, handled=self.handled)

    async def dispatch_host_alert(self, event):
        return await self._dispatch("dispatch_host_alert", event)

    async def dispatch_host_clear(self, event):
        return await self._dispatch("dispatch_host_clear", event)


@pytest.fixture
async def store(tmp_path):
    db = Database(tmp_path / "monitor.sqlite")
    await db.open()
    await apply_schema(db)
    yield MonitorStore(db)
    await db.close()


def _reading(row: dict) -> HostMetricReading:
    return HostMetricReading(
        row["component_id"],
        row["metric"],
        row["label"],
        row["value"],
        row["unit"],
        datetime.fromisoformat(row["taken_at"]),
    )


def _context(store, config, metrics_export=False):
    sensors, dispatcher = FakeSensors(), FakeDispatcher()
    metrics = MetricsConfig(export=metrics_export)
    return (
        JobContext(store=store, sensors=sensors, dispatcher=dispatcher, config=config, metrics=metrics),
        sensors,
        dispatcher,
    )


@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_scenarios(store, name):
    scenario = SCENARIOS[name]
    config = SensorsConfig.model_validate(scenario["settings"])
    ctx, sensors, dispatcher = _context(store, config)
    for step in (s for s in scenario["steps"] if "minute" in s):
        now = BASE + timedelta(minutes=step["minute"])
        sensors.readings = [_reading(r) for r in step["host"]]
        sensors.calls.clear()
        dispatcher.calls.clear()
        dispatcher.handled, dispatcher.delivered = step["handled"], step["delivered"]
        ctx.metrics.export = step["export"][0]
        scanned, _, alerts, clears = await scan_host(ctx, now)
        assert scanned == len(step["host"])
        assert [c["kernel_since"] for c in sensors.calls] == [
            datetime.fromisoformat(s) if s else None for s in step["kernel_since"]
        ]
        assert [c["export"] for c in sensors.calls] == step["export"]
        host_calls = [c for c in step["dispatched"] if c["method"].startswith("dispatch_host")]
        assert dispatcher.calls == host_calls
        delivered = step["delivered"]
        assert alerts == delivered * sum(c["method"] == "dispatch_host_alert" for c in host_calls)
        assert clears == delivered * sum(c["method"] == "dispatch_host_clear" for c in host_calls)
        db = step.get("db")
        if db is None:
            continue
        rows = await store.db.fetch_all("SELECT * FROM host_metric_states ORDER BY component_id")
        assert [{k: r[k] for k in STATE_COLUMNS} for r in rows] == sorted(
            db["host_metric_states"], key=lambda s: s["component_id"]
        )
        rows = await store.db.fetch_all("SELECT * FROM host_readings ORDER BY id")
        assert [dict(r) for r in rows] == db["host_readings"]
        state = {s["key"]: s["value"] for s in db["app_state"]}
        assert await store.get_state(KERNEL_SCAN_STATE_KEY) == state.get(KERNEL_SCAN_STATE_KEY)


async def test_empty_reading_keeps_cursor(store):
    ctx, _, dispatcher = _context(store, SensorsConfig())
    assert await scan_host(ctx, BASE) == (0, 0, 0, 0)
    assert await store.get_state(KERNEL_SCAN_STATE_KEY) is None
    assert dispatcher.calls == []


async def test_bad_cursor_does_not_fail(store):
    await store.set_state(KERNEL_SCAN_STATE_KEY, "не дата")
    ctx, sensors, _ = _context(store, SensorsConfig(host={"enabled": True}))
    sensors.readings = [_reading(SCENARIOS["host-enabled-export"]["steps"][0]["host"][0])]
    assert await scan_host(ctx, BASE) == (1, 0, 0, 0)
    assert sensors.calls == [{"kernel_since": None, "export": False}]
    assert await store.get_state(KERNEL_SCAN_STATE_KEY) == BASE.isoformat()


def test_resolver_override_needs_warn():
    config = SensorsConfig.model_validate(
        {
            "host": {
                "enabled": True,
                "thresholds": {"steal_pct": {"warn": 1, "crit": 2}, "load1": {"warn": 1, "crit": 2}},
            }
        }
    )
    resolve = host_resolver(config)
    row = {"component_id": "host:x", "label": "", "value": 1.0, "unit": "", "taken_at": BASE.isoformat()}
    steal = resolve(_reading({**row, "metric": "steal_pct"})).spec
    assert (steal.warn, steal.crit) == (1, 2)
    assert resolve(_reading({**row, "metric": "load1"})).spec.warn is None
