from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)


class Database:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database не открыта (вызовите open())")
        return self._conn

    async def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.commit()
        log.info("БД открыта: %s", self._path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            log.info("БД закрыта")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        conn = self.conn
        try:
            yield conn
        except Exception:
            await conn.rollback()
            raise
        else:
            await conn.commit()

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        async with self.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return dict(row) if row is not None else None

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        async with self.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def add_column_if_missing(db: Database, table: str, column: str, decl: str) -> bool:
    async with db.conn.execute(f"PRAGMA table_info({table})") as cur:
        existing = {row["name"] for row in await cur.fetchall()}
    if column in existing:
        return False
    await db.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    log.info("Миграция: %s.%s добавлена", table, column)
    return True
