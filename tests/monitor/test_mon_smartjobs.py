from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sa_home_node.db import Database
from sa_home_node.monitor import housekeeping, smartscan
from sa_home_node.monitor.config import SensorsConfig
from sa_home_node.monitor.db import apply_schema
from sa_home_node.monitor.hostmodels import HostMetricReading
from sa_home_node.monitor.housekeeping import HousekeepingJob
from sa_home_node.monitor.jobbase import JobContext
from sa_home_node.monitor.models import DispatchResult, SensorReading, SmartSnapshot
from sa_home_node.monitor.smartscan import SmartScanJob
from sa_home_node.monitor.store import MonitorStore

FIXTURE = Path(__file__).parents[1] / "fixtures" / "monitor" / "service" / "jobs.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
NOW = datetime(2026, 3, 1, tzinfo=UTC)


class FakeSensors:
    def __init__(self):
        self.snapshots: list[SmartSnapshot] = []

    async def read_smart_snapshots(self):
        return self.snapshots


class FakeDispatcher:
    def __init__(self):
        self.handled = True
        self.delivered = True
        self.calls: list[dict] = []

    async def dispatch_smart(self, change):
        payload = {**asdict(change), "at": change.at.isoformat()}
        payload["type"] = payload.pop("event_type")
        payload["attr_changes"] = list(payload["attr_changes"])
        self.calls.append({"method": "dispatch_smart", "payload": payload})
        return DispatchResult(delivered=self.delivered, handled=self.handled)


@pytest.fixture
async def store(tmp_path):
    db = Database(tmp_path / "monitor.sqlite")
    await db.open()
    await apply_schema(db)
    yield MonitorStore(db)
    await db.close()


def _snapshot(row: dict) -> SmartSnapshot:
    return SmartSnapshot(
        row["component_id"],
        row["label"],
        row["health"],
        {int(k): v for k, v in row["attrs"].items()},
        datetime.fromisoformat(row["taken_at"]),
    )


def test_job_identity():
    assert (SmartScanJob().dedup_key, SmartScanJob().job_type) == ("smart-scan", "smart_scan")
    assert (HousekeepingJob().dedup_key, HousekeepingJob().job_type) == ("housekeeping", "housekeeping")
    assert (smartscan.DEDUP_KEY, smartscan.JOB_TYPE) == ("smart-scan", "smart_scan")
    assert (housekeeping.KEEP_LAST_RUNS, housekeeping.KEEP_READINGS_PER_COMPONENT) == (500, 5000)
    assert housekeeping.KEEP_HOST_READINGS_PER_METRIC == 15000


async def test_smart_scan_steps(store):
    sensors, dispatcher = FakeSensors(), FakeDispatcher()
    ctx = JobContext(store=store, sensors=sensors, dispatcher=dispatcher, config=SensorsConfig())
    for step in DATA["smart_scan"]["steps"]:
        sensors.snapshots = [_snapshot(r) for r in step["snapshots"]]
        dispatcher.calls.clear()
        dispatcher.handled, dispatcher.delivered = step["handled"], step["delivered"]
        result = await SmartScanJob().run(ctx)
        assert asdict(result) == step["result"]
        assert dispatcher.calls == step["dispatched"]
        rows = await store.db.fetch_all("SELECT * FROM smart_snapshots ORDER BY component_id")
        assert [dict(r) for r in rows] == step["db"]["smart_snapshots"]


async def _count(store, table):
    return (await store.db.fetch_one(f"SELECT COUNT(*) AS n FROM {table}"))["n"]


async def test_housekeeping_prunes(store):
    fixture = DATA["housekeeping"]
    for _ in range(fixture["before"]["counts"]["job_runs"]):
        await store.start_job_run("scan", NOW)
    by_component = fixture["before"]["readings_by_component"]
    await store.record_readings(
        [SensorReading(c, "cpu", "", 1.0, NOW) for c, n in by_component.items() for _ in range(n)]
    )
    host_total = fixture["before"]["counts"]["host_readings"]
    host = [HostMetricReading("host:a", "load1", "", 1.0, "", NOW)] * (host_total - 4)
    host += [HostMetricReading("host:b", "load1", "", 1.0, "", NOW)] * 4
    await store.record_host_readings(host)

    ctx = JobContext(store=store, sensors=FakeSensors(), dispatcher=FakeDispatcher(), config=SensorsConfig())
    expected = {k: v for k, v in fixture["result"]["extra"].items() if k != "pruned_swarm_events"}
    result = await HousekeepingJob().run(ctx)
    assert result.extra == expected
    after = fixture["after"]["counts"]
    assert [await _count(store, t) for t in ("job_runs", "readings", "host_readings")] == [
        after["job_runs"],
        after["readings"],
        after["host_readings"],
    ]
    assert (await HousekeepingJob().run(ctx)).extra == {k: 0 for k in expected}
