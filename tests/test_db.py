from __future__ import annotations

from sa_home_node.db import Database, add_column_if_missing


async def test_add_column_if_missing(tmp_path) -> None:
    db = Database(tmp_path / "a.sqlite")
    await db.open()
    try:
        await db.conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        assert await add_column_if_missing(db, "t", "note", "TEXT NOT NULL DEFAULT 'x'") is True
        assert await add_column_if_missing(db, "t", "note", "TEXT") is False
        await db.conn.execute("INSERT INTO t (id) VALUES (1)")
        assert await db.fetch_all("SELECT id, note FROM t") == [{"id": 1, "note": "x"}]
    finally:
        await db.close()
