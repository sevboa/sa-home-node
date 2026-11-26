from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sa_home_node.db import Database
from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.monitor import scan
from sa_home_node.monitor.config import SensorsConfig
from sa_home_node.monitor.db import apply_schema
from sa_home_node.monitor.hostmodels import HostMetricReading
from sa_home_node.monitor.jobbase import JobContext
from sa_home_node.monitor.models import DiskSummary, DispatchResult, SensorReading, SmartSnapshot
from sa_home_node.monitor.scan import DEDUP_KEY, JOB_TYPE, SensorScanJob
from sa_home_node.monitor.store import MonitorStore

FIXTURE = Path(__file__).parents[1] / "fixtures" / "monitor" / "service" / "jobs.json"
SCENARIOS = json.loads(FIXTURE.read_text(encoding="utf-8"))["sensor_scan"]
BASE = datetime(2026, 3, 1, tzinfo=UTC)
HEALTH_COLUMNS = (
    "component_id",
    "kind",
    "label",
    "status",
    "last_temperature_c",
    "consecutive_count",
    "alerting_since",
    "first_seen_at",
    "last_seen_at",
    "notified_alert_at",
    "notified_cleared_at",
)
HOST_COLUMNS = (
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
DISKS = [
    DiskSummary("HDD1", None, 38.0, 100000000000, 320000000000, "Disk-A", "hdd"),
    DiskSummary("SSD", None, 30.0, 200000000000, 256000000000, "Disk-B", "ssd"),
]


class FakeSensors:
    def __init__(self):
        self.readings: list[SensorReading] = []
        self.host: list[HostMetricReading] = []
        self.calls: list[dict] = []
        self.overrides: list[dict] = []

    async def read_all(self):
        return self.readings

    async def read_host(self, kernel_since=None, export=False):
        self.calls.append({"kernel_since": kernel_since, "export": export})
        return self.host

    async def read_disk_summaries(self, health_overrides):
        self.overrides.append(dict(health_overrides))
        return [
            replace(d, health=health_overrides.get("/dev/sda")) if d.label == "HDD1" else d for d in DISKS
        ]


class FakeDispatcher:
    def __init__(self):
        self.handled = True
        self.delivered = True
        self.calls: list[dict] = []

    async def _dispatch(self, method, event):
        payload = {**asdict(event), "at": event.at.isoformat()}
        self.calls.append({"method": method, "payload": payload})
        return DispatchResult(delivered=self.delivered, handled=self.handled)

    async def dispatch_alert(self, event):
        return await self._dispatch("dispatch_alert", event)

    async def dispatch_clear(self, event):
        return await self._dispatch("dispatch_clear", event)

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


def _reading(row: dict) -> SensorReading:
    return SensorReading(
        row["component_id"],
        row["kind"],
        row["label"],
        row["temperature_c"],
        datetime.fromisoformat(row["taken_at"]),
    )


def _host_reading(row: dict) -> HostMetricReading:
    return HostMetricReading(
        row["component_id"],
        row["metric"],
        row["label"],
        row["value"],
        row["unit"],
        datetime.fromisoformat(row["taken_at"]),
    )


def _pick(rows, columns):
    return [{k: r[k] for k in columns} for r in rows]


@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_scenarios(store, monkeypatch, name):
    scenario = SCENARIOS[name]
    config = SensorsConfig.model_validate(scenario["settings"])
    sensors, dispatcher = FakeSensors(), FakeDispatcher()
    ctx = JobContext(store=store, sensors=sensors, dispatcher=dispatcher, config=config)
    steps = scenario["steps"]
    tail = next(s for s in steps if "override_calls" in s)
    if any(tail["override_calls"]):
        await store.save_smart_snapshot(SmartSnapshot("disk:/dev/sda", "HDD1", "warning", {}, BASE))
    for step in (s for s in steps if "minute" in s):
        now = BASE + timedelta(minutes=step["minute"])
        monkeypatch.setattr(scan, "utc_now", lambda now=now: now)
        sensors.readings = [_reading(r) for r in step["readings"]]
        sensors.host = [_host_reading(r) for r in step["host"]]
        sensors.calls.clear()
        dispatcher.calls.clear()
        dispatcher.handled, dispatcher.delivered = step["handled"], step["delivered"]
        ctx.metrics = MetricsConfig(export=step["export"][0])
        result = await SensorScanJob().run(ctx)
        assert json.loads(result.to_json()) == step["result"]
        assert dispatcher.calls == step["dispatched"]
        assert [c["kernel_since"] for c in sensors.calls] == [
            datetime.fromisoformat(s) if s else None for s in step["kernel_since"]
        ]
        assert [c["export"] for c in sensors.calls] == step["export"]
        db = step["db"]
        rows = await store.db.fetch_all("SELECT * FROM health_states ORDER BY component_id")
        assert _pick(rows, HEALTH_COLUMNS) == sorted(db["health_states"], key=lambda s: s["component_id"])
        rows = await store.db.fetch_all("SELECT * FROM host_metric_states ORDER BY component_id")
        assert _pick(rows, HOST_COLUMNS) == sorted(db["host_metric_states"], key=lambda s: s["component_id"])
        rows = await store.db.fetch_all("SELECT * FROM readings ORDER BY id")
        assert [dict(r) for r in rows] == db["readings"]
        rows = await store.db.fetch_all("SELECT * FROM host_readings ORDER BY id")
        assert [dict(r) for r in rows] == db["host_readings"]
        rows = await store.db.fetch_all("SELECT key, value FROM app_state ORDER BY key")
        assert [dict(r) for r in rows] == sorted(db["app_state"], key=lambda s: s["key"])
    assert sensors.overrides == tail["override_calls"]


async def test_disabled_disks_clear_cache(store):
    config = SensorsConfig.model_validate({"disks": {"enabled": False}})
    sensors = FakeSensors()
    ctx = JobContext(store=store, sensors=sensors, dispatcher=FakeDispatcher(), config=config)
    await store.save_disk_summaries(DISKS)
    await SensorScanJob().run(ctx)
    assert await store.get_disk_summaries() == []
    assert sensors.overrides == []


def test_job_identity():
    job = SensorScanJob()
    assert (job.dedup_key, job.job_type) == (DEDUP_KEY, JOB_TYPE) == ("sensor-scan", "sensor_scan")
