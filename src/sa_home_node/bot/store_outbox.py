from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sa_home_node.db import Database

OUTBOX_KIND_ALERT = "alert"
OUTBOX_KIND_TASK = "task"
OUTBOX_KIND_DELIVER = "deliver_message"
OUTBOX_KIND_INTERACTIVE = "interactive"

_TTL = {
    OUTBOX_KIND_ALERT: timedelta(hours=6),
    OUTBOX_KIND_TASK: timedelta(hours=24),
    OUTBOX_KIND_DELIVER: timedelta(hours=24),
    OUTBOX_KIND_INTERACTIVE: timedelta(minutes=15),
}


def outbox_ttl(kind: str) -> timedelta:
    return _TTL.get(kind, _TTL[OUTBOX_KIND_INTERACTIVE])


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


class OutboxStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def enqueue(
        self,
        chat_id: int,
        kind: str,
        payload: dict[str, object],
        *,
        now: datetime,
        dedup_key: str | None = None,
        message_thread_id: int | None = None,
    ) -> int:
        created_at = _stamp(now)
        expires_at = _stamp(now + outbox_ttl(kind))
        async with self._db.transaction() as conn:
            if dedup_key is not None:
                await conn.execute(
                    "DELETE FROM outbox WHERE chat_id = ? AND dedup_key = ?", (chat_id, dedup_key)
                )
            cursor = await conn.execute(
                "INSERT INTO outbox(chat_id, kind, payload_json, created_at, attempts, dedup_key,"
                " expires_at, message_thread_id)"
                " VALUES(?, ?, ?, ?, 0, ?, ?, ?)",
                (
                    chat_id,
                    kind,
                    json.dumps(payload, ensure_ascii=False),
                    created_at,
                    dedup_key,
                    expires_at,
                    message_thread_id,
                ),
            )
            return cursor.lastrowid

    async def due(self, now: datetime, limit: int = 100) -> list[dict[str, object]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM outbox WHERE expires_at > ? ORDER BY created_at, id LIMIT ?",
            (_stamp(now), limit),
        )
        return [
            {
                "id": row["id"],
                "chat_id": row["chat_id"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
                "created_at": datetime.fromisoformat(row["created_at"]),
                "attempts": row["attempts"],
                "dedup_key": row["dedup_key"],
                "expires_at": row["expires_at"],
                "message_thread_id": row["message_thread_id"],
            }
            for row in rows
        ]

    async def delete(self, row_id: int) -> None:
        async with self._db.transaction() as conn:
            await conn.execute("DELETE FROM outbox WHERE id = ?", (row_id,))

    async def bump_attempts(self, row_id: int) -> None:
        async with self._db.transaction() as conn:
            await conn.execute("UPDATE outbox SET attempts = attempts + 1 WHERE id = ?", (row_id,))

    async def expire(self, now: datetime) -> int:
        async with self._db.transaction() as conn:
            cursor = await conn.execute("DELETE FROM outbox WHERE expires_at <= ?", (_stamp(now),))
            return cursor.rowcount
