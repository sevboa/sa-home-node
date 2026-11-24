from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from sa_home_node.monitor.db import Database, apply_schema
from sa_home_node.monitor.hostmodels import HostHealthDiff, HostMetricState, HostTransition
from sa_home_node.monitor.models import (
    ALERTING,
    OK,
    HealthDiff,
    HealthState,
    KnownState,
    Transition,
)
from sa_home_node.monitor.store_states import StateStore

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=5)
T2 = T0 + timedelta(minutes=10)


@pytest.fixture
async def store(tmp_path):
    db = Database(tmp_path / "monitor.sqlite")
    await db.open()
    await apply_schema(db)
    yield StateStore(db)
    await db.close()


def hs(cid, status=OK, temp=40.0, kind="cpu", count=0, since=None):
    return HealthState(cid, kind, cid.upper(), status, temp, count, since)


def tr(cid, to, at, kind="cpu"):
    return Transition(cid, kind, cid.upper(), OK if to == ALERTING else ALERTING, to, 90.0, at)


def hm(cid, status=OK, value=1.0, count=0, since=None, metric="load"):
    return HostMetricState(cid, metric, cid.upper(), value, "%", status, count, since)


def htr(cid, to, at, metric="load"):
    return HostTransition(cid, metric, cid.upper(), 1.0, "%", "h", OK if to == ALERTING else ALERTING, to, at)


async def row(store, table, cid):
    return await store.db.fetch_one(f"SELECT * FROM {table} WHERE component_id=?", (cid,))


async def test_insert_and_read_back(store):
    a = hs("cpu:0", ALERTING, 91.5, count=3, since=T0)
    await store.apply_diff(HealthDiff([a, hs("disk:1", kind="disk")], [tr("cpu:0", ALERTING, T0)]), T0)
    assert await store.get_all_states() == [
        hs("cpu:0", ALERTING, 91.5, count=3, since=T0),
        hs("disk:1", kind="disk"),
    ]
    r = await row(store, "health_states", "cpu:0")
    assert (r["first_seen_at"], r["last_seen_at"]) == (T0.isoformat(), T0.isoformat())
    assert await store.get_known_states() == {
        "cpu:0": KnownState("cpu:0", ALERTING, 3, T0),
        "disk:1": KnownState("disk:1", OK, 0, None),
    }


async def test_alert_notify_clear_cycle(store):
    await store.apply_diff(HealthDiff([hs("cpu:0", ALERTING, 95, count=3, since=T0)], [tr("cpu:0", ALERTING, T0)]), T0)
    assert [s.component_id for s in await store.pending_alerts()] == ["cpu:0"]
    assert await store.pending_clears() == []
    await store.mark_alert_notified("cpu:0", T0)
    assert await store.pending_alerts() == []
    await store.apply_diff(HealthDiff([hs("cpu:0", ALERTING, 96, count=4, since=T0)], []), T1)
    r = await row(store, "health_states", "cpu:0")
    assert r["notified_alert_at"] == T0.isoformat()
    assert r["alerting_since"] == T0.isoformat()
    await store.apply_diff(HealthDiff([hs("cpu:0", OK, 50)], [tr("cpu:0", OK, T1)]), T1)
    r = await row(store, "health_states", "cpu:0")
    assert r["alerting_since"] is None and r["notified_cleared_at"] is None
    assert [s.component_id for s in await store.pending_clears()] == ["cpu:0"]
    await store.mark_cleared_notified("cpu:0", T2)
    assert await store.pending_clears() == []
    assert (await row(store, "health_states", "cpu:0"))["notified_cleared_at"] == T2.isoformat()
    await store.apply_diff(HealthDiff([hs("cpu:0", ALERTING, 99, since=T0)], [tr("cpu:0", ALERTING, T2)]), T2)
    r = await row(store, "health_states", "cpu:0")
    assert r["alerting_since"] == T2.isoformat()
    assert r["notified_alert_at"] is None and r["notified_cleared_at"] is None


async def test_host_cycle_keeps_given_alerting_since(store):
    await store.apply_host_diff(HostHealthDiff([hm("h:1", ALERTING, 99, 2, T0)], [htr("h:1", ALERTING, T1)]), T1)
    r = await row(store, "host_metric_states", "h:1")
    assert r["alerting_since"] == T0.isoformat() and r["last_value"] == 99
    assert [s.component_id for s in await store.pending_host_alerts()] == ["h:1"]
    await store.mark_host_alert_notified("h:1", T1)
    assert await store.pending_host_alerts() == []
    await store.apply_host_diff(HostHealthDiff([hm("h:1", OK, 1)], [htr("h:1", OK, T2)]), T2)
    assert [s.component_id for s in await store.pending_host_clears()] == ["h:1"]
    await store.mark_host_cleared_notified("h:1", T2)
    assert await store.pending_host_clears() == []
    assert await store.get_all_host_states() == [hm("h:1", OK, 1)]
    assert (await store.get_known_host_states())["h:1"].status == OK


@pytest.mark.parametrize(
    ("scanned", "left"),
    [
        (["cpu:0"], ["cpu:0", "disk:1", "disk:2"]),
        (["disk:1"], ["cpu:0", "disk:1"]),
        (["cpu:0", "disk:2"], ["cpu:0", "disk:2"]),
    ],
)
async def test_health_prune_scoped_by_kind(store, scanned, left):
    seed = [hs("cpu:0"), hs("disk:1", kind="disk"), hs("disk:2", kind="disk")]
    await store.apply_diff(HealthDiff(seed, []), T0)
    states = [hs(c, kind=c.split(":")[0]) for c in scanned]
    await store.apply_diff(HealthDiff(states, []), T1)
    assert sorted(s.component_id for s in await store.get_all_states()) == left


async def test_host_prune_global_and_empty_noop(store):
    await store.apply_host_diff(HostHealthDiff([hm("a"), hm("b", metric="mem")], []), T0)
    await store.apply_host_diff(HostHealthDiff([], []), T1)
    assert len(await store.get_all_host_states()) == 2
    await store.apply_host_diff(HostHealthDiff([hm("a")], []), T1)
    assert [s.component_id for s in await store.get_all_host_states()] == ["a"]


async def test_failed_transaction_rolls_back(store):
    bad = hs("cpu:0")
    object.__setattr__(bad, "kind", None)
    with pytest.raises(sqlite3.IntegrityError):
        await store.apply_diff(HealthDiff([hs("cpu:9"), bad], []), T0)
    assert await store.get_all_states() == []
