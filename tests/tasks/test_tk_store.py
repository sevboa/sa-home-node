from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sa_home_node.db import Database
from sa_home_node.tasks.store import TaskStore, apply_schema

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

TASK_COLUMNS = [
    ("id", "INTEGER", 0, 1),
    ("dst_node", "TEXT", 1, 0),
    ("dst_service", "TEXT", 1, 0),
    ("action", "TEXT", 1, 0),
    ("args_json", "TEXT", 1, 0),
    ("timeout_s", "REAL", 1, 0),
    ("meta_json", "TEXT", 1, 0),
    ("due_at", "TEXT", 1, 0),
    ("created_at", "TEXT", 1, 0),
    ("fired_at", "TEXT", 0, 0),
    ("prewake_done", "INTEGER", 1, 0),
]


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "tasks.sqlite")
    await database.open()
    await apply_schema(database)
    yield database
    await database.close()


@pytest.fixture
def store(db):
    return TaskStore(db)


async def new_task(store, due, action="act", **extra):
    return await store.create_task("n", "svc", action, {"a": "я"}, 5.0, {"m": 1}, due, NOW)


async def test_schema_matches_prototype(db):
    cols = await db.fetch_all("PRAGMA table_info(tasks)")
    assert [(c["name"], c["type"], c["notnull"], c["pk"]) for c in cols] == TASK_COLUMNS
    assert cols[-1]["dflt_value"] == "0"
    waiters = await db.fetch_all("PRAGMA table_info(event_waiters)")
    assert [(c["name"], c["type"], c["notnull"], c["pk"]) for c in waiters] == [
        ("task_id", "INTEGER", 0, 1),
        ("node", "TEXT", 1, 0),
        ("event_type", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
    ]
    state = await db.fetch_all("PRAGMA table_info(app_state)")
    assert [(c["name"], c["type"], c["notnull"], c["pk"]) for c in state] == [
        ("key", "TEXT", 0, 1),
        ("value", "TEXT", 1, 0),
    ]
    tasks_idx = await db.fetch_all("PRAGMA index_list(tasks)")
    assert [(i["name"], i["partial"]) for i in tasks_idx if i["name"] == "idx_tasks_due"] == [
        ("idx_tasks_due", 1)
    ]
    waiters_idx = await db.fetch_all("PRAGMA index_list(event_waiters)")
    assert "idx_event_waiters_match" in {i["name"] for i in waiters_idx}


async def test_apply_schema_idempotent_and_keeps_foreign_tables(db, store):
    async with db.transaction() as conn:
        await conn.execute("CREATE TABLE other(x INTEGER)")
        await conn.execute("INSERT INTO other VALUES(7)")
    await new_task(store, NOW)
    await apply_schema(db)
    assert (await db.fetch_one("SELECT x FROM other")) == {"x": 7}
    assert len(await db.fetch_all("SELECT id FROM tasks")) == 1


async def test_create_task_serialization(store, db):
    task_id = await new_task(store, NOW + timedelta(seconds=5))
    row = await db.fetch_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    assert row["args_json"] == '{"a": "я"}'
    assert row["meta_json"] == '{"m": 1}'
    assert row["due_at"] == "2026-01-01T12:00:05+00:00"
    assert row["created_at"] == "2026-01-01T12:00:00+00:00"
    assert row["fired_at"] is None
    assert row["prewake_done"] == 0


async def test_due_and_fired(store):
    early = await new_task(store, NOW - timedelta(seconds=10))
    late = await new_task(store, NOW + timedelta(hours=1))
    first = await new_task(store, NOW - timedelta(seconds=20))
    assert [t["id"] for t in await store.due_tasks(NOW)] == [first, early]
    assert (await store.get_pending_task(late))["id"] == late
    await store.mark_task_fired(early, NOW)
    assert [t["id"] for t in await store.due_tasks(NOW)] == [first]
    assert await store.get_pending_task(early) is None
    assert await store.get_pending_task(999) is None


async def test_prewake(store):
    soon = await new_task(store, NOW + timedelta(minutes=5))
    far = await new_task(store, NOW + timedelta(hours=2))
    fired = await new_task(store, NOW + timedelta(minutes=1))
    await store.mark_task_fired(fired, NOW)
    deadline = NOW + timedelta(minutes=10)
    assert [t["id"] for t in await store.tasks_needing_prewake(deadline)] == [soon]
    await store.mark_task_prewake_done(soon)
    assert await store.tasks_needing_prewake(deadline) == []
    assert [t["id"] for t in await store.tasks_needing_prewake(NOW + timedelta(hours=3))] == [far]


async def test_event_waiters(store):
    await store.add_event_waiter(5, "n1", "ev", NOW)
    await store.add_event_waiter(3, "n1", "ev", NOW)
    await store.add_event_waiter(4, "n2", "ev", NOW)
    assert await store.pop_event_waiter_for("n1", "other") is None
    assert await store.pop_event_waiter_for("n1", "ev") == 3
    assert await store.pop_event_waiter_for("n1", "ev") == 5
    assert await store.pop_event_waiter_for("n1", "ev") is None
    await store.pop_event_waiter(4)
    assert await store.pop_event_waiter_for("n2", "ev") is None


async def test_state(store):
    assert await store.get_state("k") is None
    await store.set_state("k", "v1")
    await store.set_state("k", "v2")
    assert await store.get_state("k") == "v2"
