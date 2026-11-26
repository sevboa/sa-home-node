from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from sa_home_node.db import Database
from sa_home_node.monitor.db import apply_schema
from sa_home_node.monitor.hostmodels import HostMetricReading
from sa_home_node.monitor.models import DiskSummary, SensorReading, SmartSnapshot
from sa_home_node.monitor.store_data import DISK_SUMMARIES_KEY, DataStore

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
async def store(tmp_path):
    db = Database(tmp_path / "m.sqlite")
    await db.open()
    await apply_schema(db)
    yield DataStore(db)
    await db.close()


def reading(cid, t, i=0):
    return SensorReading(cid, "cpu", "CPU", t, T0 + timedelta(minutes=i))


def host(cid, v, minutes):
    metric = cid.removeprefix("host:")
    return HostMetricReading(cid, metric, metric, v, "%", T0 + timedelta(minutes=minutes))


async def test_state_roundtrip(store):
    assert await store.get_state("k") is None
    await store.set_state("k", "1")
    await store.set_state("k", "2")
    assert await store.get_state("k") == "2"


async def test_job_runs(store):
    a = await store.start_job_run("sensor_scan", T0)
    b = await store.start_job_run("smart_scan", T0)
    await store.finish_job_run(a, "ok", T0, metrics_json="{}")
    await store.finish_job_run(b, "error", T0, error="boom")
    runs = await store.recent_job_runs(1)
    assert [r["id"] for r in runs] == [b]
    assert runs[0]["error"] == "boom" and runs[0]["finished_at"] == T0.isoformat()
    assert await store.job_run_counts() == {"ok": 1, "error": 1}
    assert await store.prune_job_runs(1) == 1
    assert [r["id"] for r in await store.recent_job_runs()] == [b]


@pytest.mark.parametrize(
    ("record", "prune", "table", "make"),
    [
        ("record_readings", "prune_readings", "readings", lambda c, i: reading(c, 40.0, i)),
        (
            "record_host_readings",
            "prune_host_readings",
            "host_readings",
            lambda c, i: host(c, 1.0, i),
        ),
    ],
)
async def test_record_and_prune(store, record, prune, table, make):
    items = [make(c, i) for c in ("a", "b") for i in range(5)]
    await getattr(store, record)([])
    await getattr(store, record)(items)
    assert await getattr(store, prune)(2) == 6
    rows = await store.db.fetch_all(f"SELECT component_id, taken_at FROM {table} ORDER BY id")
    assert [r["component_id"] for r in rows] == ["a", "a", "b", "b"]
    assert rows[0]["taken_at"] == (T0 + timedelta(minutes=3)).isoformat()


async def test_host_trend(store):
    values = [(0, 10.0), (5, 30.0), (12, 50.0), (-1000, 99.0)]
    await store.record_host_readings([host("host:cpu_load", v, m) for m, v in values])
    await store.record_host_readings([host("host:other", 1.0, 0)])
    trend = await store.host_trend("cpu_load", 1, T0 + timedelta(minutes=30))
    assert trend == [
        {"bucket": "2026-01-01T12:00", "avg": 20.0, "max": 30.0, "samples": 2},
        {"bucket": "2026-01-01T12:10", "avg": 50.0, "max": 50.0, "samples": 1},
    ]


async def test_baseline_stats(store):
    empty = await store.baseline_stats("c", 10)
    assert (empty.count, empty.mean, empty.std) == (0, 0.0, 0.0)
    temps = [100.0, 40.0, 50.0, 60.0]
    await store.record_readings([reading("c", t, i) for i, t in enumerate(temps)])
    stats = await store.baseline_stats("c", 3)
    assert stats.count == 3 and stats.mean == pytest.approx(50.0)
    assert stats.std == pytest.approx((200 / 3) ** 0.5)
    await store.record_readings([reading("d", 7.0, i) for i in range(3)])
    assert (await store.baseline_stats("d", 10)).std == 0.0


async def test_smart_snapshots(store):
    assert await store.get_smart_snapshot("disk:/dev/a") is None
    snap = SmartSnapshot("disk:/dev/a", "HDD1", "PASSED", {5: 1, 197: 0}, T0)
    await store.save_smart_snapshot(snap)
    await store.save_smart_snapshot(SmartSnapshot("disk:/dev/b", "SSD", None, {}, T0))
    assert await store.get_smart_snapshot("disk:/dev/a") == snap
    newer = SmartSnapshot("disk:/dev/a", "HDD1", "FAILED", {5: 2}, T0 + timedelta(hours=1))
    await store.save_smart_snapshot(newer)
    assert await store.get_smart_snapshot("disk:/dev/a") == newer
    assert await store.get_smart_health_map() == {"/dev/a": "FAILED", "/dev/b": None}


async def test_disk_summaries(store):
    assert await store.get_disk_summaries() is None
    disks = [
        DiskSummary("HDD1", "PASSED", 35.0, 10, 100, "ST-A", "hdd"),
        DiskSummary("NVMe", None, None, None, None),
    ]
    await store.save_disk_summaries(disks)
    assert await store.get_disk_summaries() == disks
    assert json.loads(await store.get_state(DISK_SUMMARIES_KEY))[0]["label"] == "HDD1"
