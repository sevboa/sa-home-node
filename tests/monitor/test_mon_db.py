from __future__ import annotations

import sqlite3

import pytest

from sa_home_node.monitor.db import Database, apply_schema

TABLES = {
    "job_runs": [
        (0, "id", "INTEGER", 0, None, 1),
        (1, "job_type", "TEXT", 1, None, 0),
        (2, "started_at", "TEXT", 1, None, 0),
        (3, "finished_at", "TEXT", 0, None, 0),
        (4, "status", "TEXT", 1, "'running'", 0),
        (5, "error", "TEXT", 0, None, 0),
        (6, "metrics_json", "TEXT", 0, None, 0),
    ],
    "app_state": [
        (0, "key", "TEXT", 0, None, 1),
        (1, "value", "TEXT", 1, None, 0),
    ],
    "health_states": [
        (0, "component_id", "TEXT", 0, None, 1),
        (1, "kind", "TEXT", 1, None, 0),
        (2, "label", "TEXT", 1, None, 0),
        (3, "status", "TEXT", 1, None, 0),
        (4, "last_temperature_c", "REAL", 0, None, 0),
        (5, "consecutive_count", "INTEGER", 1, "0", 0),
        (6, "alerting_since", "TEXT", 0, None, 0),
        (7, "first_seen_at", "TEXT", 1, None, 0),
        (8, "last_seen_at", "TEXT", 1, None, 0),
        (9, "notified_alert_at", "TEXT", 0, None, 0),
        (10, "notified_cleared_at", "TEXT", 0, None, 0),
    ],
    "readings": [
        (0, "id", "INTEGER", 0, None, 1),
        (1, "component_id", "TEXT", 1, None, 0),
        (2, "temperature_c", "REAL", 1, None, 0),
        (3, "taken_at", "TEXT", 1, None, 0),
    ],
    "host_metric_states": [
        (0, "component_id", "TEXT", 0, None, 1),
        (1, "metric", "TEXT", 1, None, 0),
        (2, "label", "TEXT", 1, None, 0),
        (3, "unit", "TEXT", 1, "''", 0),
        (4, "status", "TEXT", 1, None, 0),
        (5, "last_value", "REAL", 0, None, 0),
        (6, "consecutive_count", "INTEGER", 1, "0", 0),
        (7, "alerting_since", "TEXT", 0, None, 0),
        (8, "first_seen_at", "TEXT", 1, None, 0),
        (9, "last_seen_at", "TEXT", 1, None, 0),
        (10, "notified_alert_at", "TEXT", 0, None, 0),
        (11, "notified_cleared_at", "TEXT", 0, None, 0),
    ],
    "host_readings": [
        (0, "id", "INTEGER", 0, None, 1),
        (1, "component_id", "TEXT", 1, None, 0),
        (2, "metric", "TEXT", 1, None, 0),
        (3, "value", "REAL", 1, None, 0),
        (4, "taken_at", "TEXT", 1, None, 0),
    ],
    "smart_snapshots": [
        (0, "component_id", "TEXT", 0, None, 1),
        (1, "label", "TEXT", 1, None, 0),
        (2, "health", "TEXT", 0, None, 0),
        (3, "attrs_json", "TEXT", 1, None, 0),
        (4, "taken_at", "TEXT", 1, None, 0),
    ],
}

INDEXES = {
    "readings": {"idx_readings_component": ["component_id", "id"]},
    "host_readings": {"idx_host_readings_metric": ["component_id", "taken_at"]},
}


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "sub" / "monitor.sqlite")
    await database.open()
    yield database
    await database.close()


async def test_pragmas_and_dir(db, tmp_path):
    assert (tmp_path / "sub").is_dir()
    assert (await db.fetch_one("PRAGMA journal_mode"))["journal_mode"] == "wal"
    assert (await db.fetch_one("PRAGMA foreign_keys"))["foreign_keys"] == 1
    assert (await db.fetch_one("PRAGMA busy_timeout"))["timeout"] == 5000


async def test_conn_before_open(tmp_path):
    with pytest.raises(RuntimeError):
        _ = Database(tmp_path / "x.sqlite").conn


async def test_schema_matches_prototype(db):
    await apply_schema(db)
    names = {r["name"] for r in await db.fetch_all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    assert names == set(TABLES)
    for table, expected in TABLES.items():
        rows = await db.fetch_all(f"PRAGMA table_info({table})")
        got = [(r["cid"], r["name"], r["type"], r["notnull"], r["dflt_value"], r["pk"]) for r in rows]
        assert got == expected, table
        idx = {}
        for r in await db.fetch_all(f"PRAGMA index_list({table})"):
            if r["origin"] != "c":
                continue
            cols = await db.fetch_all(f"PRAGMA index_info({r['name']})")
            idx[r["name"]] = [c["name"] for c in cols]
        assert idx == INDEXES.get(table, {}), table


async def test_apply_schema_idempotent(db):
    await apply_schema(db)
    await db.conn.execute("INSERT INTO app_state(key, value) VALUES ('k', 'v')")
    await db.conn.commit()
    before = await db.fetch_all("SELECT type, name, sql FROM sqlite_master ORDER BY name")
    await apply_schema(db)
    assert await db.fetch_all("SELECT type, name, sql FROM sqlite_master ORDER BY name") == before
    assert await db.fetch_one("SELECT value FROM app_state WHERE key=?", ("k",)) == {"value": "v"}


async def test_foreign_tables_untouched(tmp_path):
    path = tmp_path / "monitor.sqlite"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT)")
    raw.execute("INSERT INTO tasks(title) VALUES ('a')")
    raw.commit()
    raw.close()
    database = Database(path)
    await database.open()
    try:
        await apply_schema(database)
        assert await database.fetch_all("SELECT id, title FROM tasks") == [{"id": 1, "title": "a"}]
    finally:
        await database.close()


async def test_fetch_helpers(db):
    await apply_schema(db)
    assert await db.fetch_one("SELECT * FROM app_state") is None
    assert await db.fetch_all("SELECT * FROM app_state") == []
    async with db.transaction() as conn:
        await conn.execute("INSERT INTO app_state(key, value) VALUES (?, ?)", ("a", "1"))
        await conn.execute("INSERT INTO app_state(key, value) VALUES (?, ?)", ("b", "2"))
    rows = await db.fetch_all("SELECT key, value FROM app_state ORDER BY key")
    assert rows == [{"key": "a", "value": "1"}, {"key": "b", "value": "2"}]


async def test_transaction_rollback(db):
    await apply_schema(db)
    with pytest.raises(ValueError):
        async with db.transaction() as conn:
            await conn.execute("INSERT INTO app_state(key, value) VALUES ('a', '1')")
            raise ValueError("boom")
    assert await db.fetch_all("SELECT * FROM app_state") == []


async def test_close_idempotent(tmp_path):
    database = Database(tmp_path / "m.sqlite")
    await database.open()
    await database.close()
    await database.close()
    with pytest.raises(RuntimeError):
        _ = database.conn
