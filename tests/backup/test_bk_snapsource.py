from __future__ import annotations

import asyncio
import base64
import sqlite3

import aiosqlite
import pytest

from sa_home_node.backup import hold, sealed, snapdoc, snapsource
from sa_home_node.backup.config import BackupConfig
from sa_home_node.backup.hold import HOLD_HINT
from sa_home_node.backup.snapdoc import SnapshotError
from sa_home_node.backup.spec import BackupSpec

FMT = "test/snapshot/1"


async def _noop(*_):
    return {}


def make_spec():
    return BackupSpec(
        service="svc",
        identity_service="svc-id",
        identity_format="test/identity/1",
        snapshot_format=FMT,
        bundle_format="test/bundle/1",
        tables=("tbl_a",),
        skip_tables=frozenset(),
        primary_table="tbl_a",
        trigger_actions=frozenset(),
        collect_identity=_noop,
        apply=_noop,
        summarize=lambda b: [],
        rows_extra=lambda t: {"tbl_a_active": sum(1 for r in t["tbl_a"]["rows"] if r[1] == "active")},
    )


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
async def conn(tmp_path):
    async with aiosqlite.connect(tmp_path / "db.sqlite") as c:
        c.row_factory = sqlite3.Row
        await c.execute("CREATE TABLE tbl_a (id TEXT PRIMARY KEY, status TEXT)")
        await c.execute("INSERT INTO tbl_a VALUES ('a','active')")
        yield c


@pytest.fixture
def keys():
    return sealed.generate_keypair()


def make_source(tmp_path, conn, keys, clock, **kw):
    cfg = BackupConfig(
        recipient_public_key=sealed.dump_key(keys[1]),
        snapshot_interval_s=600.0,
        snapshot_debounce_s=15.0,
    )
    return snapsource.SnapshotSource(
        make_spec(), cfg, tmp_path / "node.sqlite", lambda: conn, "node-a", clock=clock, **kw
    )


async def test_build_meta_and_document(tmp_path, conn, keys):
    src = make_source(tmp_path, conn, keys, Clock())
    snap = await src.current()
    assert snap.meta["source"] == "node-a"
    assert snap.meta["format"] == FMT
    assert snap.meta["rev"] == 1
    assert snap.meta["hash"].startswith("sha256:")
    assert snap.meta["rows"] == {"tbl_a": 1, "tbl_a_active": 1}
    doc = snapdoc.open_snapshot(FMT, keys[0], snap.blob)
    assert doc["tables"]["tbl_a"]["rows"] == [["a", "active"]]


async def test_interval_debounce_and_unchanged_hash(tmp_path, conn, keys):
    clock = Clock()
    src = make_source(tmp_path, conn, keys, clock)
    first = await src.current()
    clock.now += 10
    await conn.execute("INSERT INTO tbl_a VALUES ('b','off')")
    assert await src.current() is first
    src._dirty = True
    assert await src.current() is first
    clock.now += 6
    second = await src.current()
    assert second is not first
    assert second.meta["rev"] == 2
    assert second.meta["rows"]["tbl_a"] == 2
    clock.now += 700
    third = await src.current()
    assert third is second
    assert third.meta["rev"] == 2


async def test_failure_keeps_cache(tmp_path, conn, keys):
    clock = Clock()
    src = make_source(tmp_path, conn, keys, clock)
    first = await src.current()
    clock.now += 700

    def boom():
        raise RuntimeError("db gone")

    src._conn_getter = boom
    assert await src.current() is first
    fresh = make_source(tmp_path, conn, keys, clock)
    fresh._conn_getter = boom
    assert await fresh.current() is None


async def test_oversize(tmp_path, conn, keys, monkeypatch):
    monkeypatch.setattr(snapsource, "MAX_SEALED_BYTES", 10)
    src = make_source(tmp_path, conn, keys, Clock())
    assert await src.current() is None
    with pytest.raises(SnapshotError, match="не лезет в сообщение"):
        await src._rebuild()


async def test_handle_get(tmp_path, conn, keys):
    src = make_source(tmp_path, conn, keys, Clock())
    with pytest.raises(SnapshotError) as exc:
        await src.handle_get({})
    assert str(exc.value) == HOLD_HINT
    hold.allow_publish(tmp_path / "node.sqlite", "test")
    resp = await src.handle_get({})
    snap = await src.current()
    assert resp["hash"] == snap.meta["hash"]
    assert resp["meta"] == snap.meta
    assert base64.b64decode(resp["sealed"]) == snap.blob
    assert await src.handle_get({"have_hash": snap.meta["hash"]}) == {
        "unchanged": True,
        "hash": snap.meta["hash"],
    }
    assert "sealed" in await src.handle_get({"have_hash": "sha256:other"})


async def test_handle_get_not_built(tmp_path, conn, keys):
    hold.allow_publish(tmp_path / "node.sqlite", "test")
    src = make_source(tmp_path, conn, keys, Clock())

    def boom():
        raise RuntimeError("x")

    src._conn_getter = boom
    with pytest.raises(SnapshotError, match="ещё не собран"):
        await src.handle_get({})


async def test_touch_flushes_and_notifies(tmp_path, conn, keys):
    calls = []

    async def notify():
        calls.append(1)

    cfg = BackupConfig(recipient_public_key=sealed.dump_key(keys[1]), snapshot_debounce_s=0.01)
    src = snapsource.SnapshotSource(
        make_spec(), cfg, tmp_path / "node.sqlite", lambda: conn, "node-a", notify=notify
    )
    src.touch()
    first_task = src._pending
    src.touch()
    assert src._pending is first_task
    await first_task
    assert calls == [1]
    assert src._cached is not None
    await src.stop()


async def test_notify_failure_swallowed_and_stop_cancels(tmp_path, conn, keys):
    async def notify():
        raise OSError("down")

    cfg = BackupConfig(recipient_public_key=sealed.dump_key(keys[1]), snapshot_debounce_s=0.01)
    src = snapsource.SnapshotSource(
        make_spec(), cfg, tmp_path / "node.sqlite", lambda: conn, "node-a", notify=notify
    )
    src.touch()
    await src._pending
    cfg2 = BackupConfig(recipient_public_key=sealed.dump_key(keys[1]), snapshot_debounce_s=60.0)
    src2 = snapsource.SnapshotSource(make_spec(), cfg2, tmp_path / "n.sqlite", lambda: conn, "node-a")
    src2.touch()
    await asyncio.sleep(0)
    await src2.stop()
    assert src2._pending is None


def test_bad_recipient_key(tmp_path, conn):
    with pytest.raises(sealed.SealedError):
        snapsource.SnapshotSource(
            make_spec(), BackupConfig(recipient_public_key="bad"), tmp_path / "n", lambda: conn, "n"
        )
