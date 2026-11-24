from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type     TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    error        TEXT,
    metrics_json TEXT
);

CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS health_states (
    component_id          TEXT PRIMARY KEY,
    kind                  TEXT NOT NULL,
    label                 TEXT NOT NULL,
    status                TEXT NOT NULL,
    last_temperature_c    REAL,
    consecutive_count     INTEGER NOT NULL DEFAULT 0,
    alerting_since        TEXT,
    first_seen_at         TEXT NOT NULL,
    last_seen_at          TEXT NOT NULL,
    notified_alert_at     TEXT,
    notified_cleared_at   TEXT
);

CREATE TABLE IF NOT EXISTS readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    component_id  TEXT NOT NULL,
    temperature_c REAL NOT NULL,
    taken_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_readings_component ON readings(component_id, id);

CREATE TABLE IF NOT EXISTS host_metric_states (
    component_id         TEXT PRIMARY KEY,
    metric               TEXT NOT NULL,
    label                TEXT NOT NULL,
    unit                 TEXT NOT NULL DEFAULT '',
    status               TEXT NOT NULL,
    last_value           REAL,
    consecutive_count    INTEGER NOT NULL DEFAULT 0,
    alerting_since       TEXT,
    first_seen_at        TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    notified_alert_at    TEXT,
    notified_cleared_at  TEXT
);

CREATE TABLE IF NOT EXISTS host_readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    component_id  TEXT NOT NULL,
    metric        TEXT NOT NULL,
    value         REAL NOT NULL,
    taken_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_host_readings_metric ON host_readings(component_id, taken_at);

CREATE TABLE IF NOT EXISTS smart_snapshots (
    component_id  TEXT PRIMARY KEY,
    label         TEXT NOT NULL,
    health        TEXT,
    attrs_json    TEXT NOT NULL,
    taken_at      TEXT NOT NULL
);
"""


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


async def apply_schema(db: Database) -> None:
    await db.conn.executescript(SCHEMA)
    await db.conn.commit()
