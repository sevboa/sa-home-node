from __future__ import annotations

import shutil

import pytest
from botfix import FIX, load
from sa_home_node.bot.db import AppState, open_bot_db

SCHEMA = load("db/schema.json")["tables"]
SEEDED = load("db/seeded.json")
OPS = load("db/app_state.json")


async def table_info(db, table):
    rows = await db.fetch_all(f"PRAGMA table_info({table})")
    return [
        {
            "name": r["name"],
            "type": r["type"],
            "notnull": r["notnull"],
            "default": r["dflt_value"],
            "pk": r["pk"],
        }
        for r in rows
    ]


async def indexes(db, table):
    result = []
    for r in await db.fetch_all(f"PRAGMA index_list({table})"):
        if r["origin"] == "pk" or r["name"].startswith("sqlite_autoindex"):
            continue
        columns = [c["name"] for c in await db.fetch_all(f"PRAGMA index_info({r['name']})")]
        sql = (await db.fetch_one("SELECT sql FROM sqlite_master WHERE name=?", (r["name"],)))["sql"]
        where = sql.upper().split(" WHERE ", 1)
        result.append(
            {
                "name": r["name"],
                "unique": bool(r["unique"]),
                "columns": columns,
                "where": " ".join(sql[len(sql) - len(where[1]) :].split()) if len(where) == 2 else None,
            }
        )
    return sorted(result, key=lambda i: i["name"])


@pytest.mark.parametrize("table", sorted(SCHEMA))
async def test_schema_equals_prototype(tmp_path, table):
    db = await open_bot_db(tmp_path / "bot.sqlite", ())
    try:
        assert await table_info(db, table) == SCHEMA[table]["columns"]
        assert await indexes(db, table) == SCHEMA[table]["indexes"]
        assert await db.fetch_all(f"PRAGMA foreign_key_list({table})") == []
    finally:
        await db.close()


async def test_plugin_schema_applied_and_idempotent(tmp_path):
    path = tmp_path / "bot.sqlite"
    extra = ("CREATE TABLE IF NOT EXISTS plug_a(id INTEGER PRIMARY KEY, v TEXT);",)
    for _ in range(2):
        db = await open_bot_db(path, extra)
        await db.close()
    db = await open_bot_db(path, extra)
    try:
        assert await db.fetch_one("SELECT name FROM sqlite_master WHERE name='plug_a'")
    finally:
        await db.close()


async def test_opens_prototype_database(tmp_path):
    path = tmp_path / "sentinel.sqlite"
    shutil.copy(FIX / "db" / "seeded-sentinel.sqlite", path)
    db = await open_bot_db(path, ())
    try:
        state = AppState(db)
        keys = await state.keys("")
        assert [[k, await state.get(k)] for k in keys] == SEEDED["app_state"]
        for table in ("outbox", "invites", "swarm_events", "health_notifications"):
            row = await db.fetch_one(f"SELECT count(*) AS n FROM {table}")
            assert row["n"] == SEEDED[table]
        for table in SEEDED["foreign_tables"]:
            assert await db.fetch_one("SELECT name FROM sqlite_master WHERE name=?", (table,))
    finally:
        await db.close()


async def test_app_state_matches_prototype(tmp_path):
    db = await open_bot_db(tmp_path / "bot.sqlite", ())
    try:
        state = AppState(db)
        for op, expected in zip(OPS["ops"], OPS["results"], strict=True):
            if op[0] == "set":
                assert await state.set(op[1], op[2]) is None
            elif op[0] == "get":
                assert await state.get(op[1]) == expected
            elif op[0] == "delete":
                assert await state.delete(op[1]) is None
            else:
                assert await state.keys(op[1]) == expected
    finally:
        await db.close()


async def test_compare_and_set(tmp_path):
    db = await open_bot_db(tmp_path / "bot.sqlite", ())
    try:
        state = AppState(db)
        steps = [
            (None, "1", True, "1"),
            (None, "2", False, "1"),
            ("1", "2", True, "2"),
            ("x", "3", False, "2"),
            ("2", None, True, None),
            ("2", None, False, None),
        ]
        for old, new, changed, value in steps:
            assert await state.compare_and_set("k", old, new) is changed
            assert await state.get("k") == value
    finally:
        await db.close()
