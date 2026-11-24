from __future__ import annotations

import json
from datetime import datetime
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dst_node     TEXT NOT NULL,
    dst_service  TEXT NOT NULL,
    action       TEXT NOT NULL,
    args_json    TEXT NOT NULL,
    timeout_s    REAL NOT NULL,
    meta_json    TEXT NOT NULL,
    due_at       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    fired_at     TEXT,
    prewake_done INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_at) WHERE fired_at IS NULL;
CREATE TABLE IF NOT EXISTS event_waiters (
    task_id     INTEGER PRIMARY KEY,
    node        TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_waiters_match ON event_waiters(node, event_type);
"""


def iso_or_none(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


async def apply_schema(db: Any) -> None:
    async with db.transaction() as conn:
        await conn.executescript(SCHEMA)


class TaskStore:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def create_task(
        self,
        dst_node: str,
        dst_service: str,
        action: str,
        args: dict,
        timeout_s: float,
        meta: dict,
        due_at: datetime,
        created_at: datetime,
    ) -> int:
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "INSERT INTO tasks(dst_node, dst_service, action, args_json, timeout_s, "
                "meta_json, due_at, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dst_node,
                    dst_service,
                    action,
                    json.dumps(args, ensure_ascii=False),
                    timeout_s,
                    json.dumps(meta, ensure_ascii=False),
                    iso_or_none(due_at),
                    iso_or_none(created_at),
                ),
            )
            return cur.lastrowid

    async def due_tasks(self, now: datetime) -> list[dict]:
        return await self.db.fetch_all(
            "SELECT * FROM tasks WHERE fired_at IS NULL AND due_at<=? ORDER BY due_at",
            (iso_or_none(now),),
        )

    async def get_pending_task(self, task_id: int) -> dict | None:
        return await self.db.fetch_one(
            "SELECT * FROM tasks WHERE id=? AND fired_at IS NULL", (task_id,)
        )

    async def mark_task_fired(self, task_id: int, at: datetime) -> None:
        async with self.db.transaction() as conn:
            await conn.execute("UPDATE tasks SET fired_at=? WHERE id=?", (iso_or_none(at), task_id))

    async def tasks_needing_prewake(self, deadline: datetime) -> list[dict]:
        return await self.db.fetch_all(
            "SELECT * FROM tasks WHERE fired_at IS NULL AND prewake_done=0 AND due_at<=?",
            (iso_or_none(deadline),),
        )

    async def mark_task_prewake_done(self, task_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute("UPDATE tasks SET prewake_done=1 WHERE id=?", (task_id,))

    async def add_event_waiter(
        self, task_id: int, node: str, event_type: str, created_at: datetime
    ) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "INSERT INTO event_waiters(task_id, node, event_type, created_at) "
                "VALUES(?, ?, ?, ?)",
                (task_id, node, event_type, iso_or_none(created_at)),
            )

    async def pop_event_waiter_for(self, node: str, event_type: str) -> int | None:
        async with self.db.transaction() as conn:
            cur = await conn.execute(
                "SELECT task_id FROM event_waiters WHERE node=? AND event_type=? "
                "ORDER BY task_id LIMIT 1",
                (node, event_type),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            task_id = row["task_id"]
            await conn.execute("DELETE FROM event_waiters WHERE task_id=?", (task_id,))
            return task_id

    async def pop_event_waiter(self, task_id: int) -> None:
        async with self.db.transaction() as conn:
            await conn.execute("DELETE FROM event_waiters WHERE task_id=?", (task_id,))

    async def get_state(self, key: str) -> str | None:
        row = await self.db.fetch_one("SELECT value FROM app_state WHERE key=?", (key,))
        return row["value"] if row else None

    async def set_state(self, key: str, value: str) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "INSERT INTO app_state(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
