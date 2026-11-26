from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from sa_home_node.db import Database

SCHEMA = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")


async def open_bot_db(path: str | Path, extra_schema: Sequence[str] = ()) -> Database:
    db = Database(path)
    await db.open()
    async with db.transaction() as conn:
        for sql in (SCHEMA, *extra_schema):
            await conn.executescript(sql)
    return db


class AppState:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str) -> str | None:
        row = await self._db.fetch_one("SELECT value FROM app_state WHERE key = ?", (key,))
        return row["value"] if row else None

    async def set(self, key: str, value: str) -> None:
        await self._write(
            "INSERT INTO app_state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    async def delete(self, key: str) -> None:
        await self._write("DELETE FROM app_state WHERE key = ?", (key,))

    async def keys(self, prefix: str) -> list[str]:
        rows = await self._db.fetch_all(
            "SELECT key FROM app_state WHERE substr(key, 1, ?) = ? ORDER BY key", (len(prefix), prefix)
        )
        return [row["key"] for row in rows]

    async def compare_and_set(self, key: str, old: str | None, new: str | None) -> bool:
        if old is None and new is None:
            return False
        if old is None:
            sql, params = (
                "INSERT INTO app_state(key, value) VALUES(?, ?) ON CONFLICT(key) DO NOTHING",
                (key, new),
            )
        elif new is None:
            sql, params = "DELETE FROM app_state WHERE key = ? AND value = ?", (key, old)
        else:
            sql, params = "UPDATE app_state SET value = ? WHERE key = ? AND value = ?", (new, key, old)
        return await self._write(sql, params) == 1

    async def _write(self, sql: str, params: Sequence[object]) -> int:
        async with self._db.transaction() as conn:
            cursor = await conn.execute(sql, params)
            return cursor.rowcount
