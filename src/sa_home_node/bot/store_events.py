from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sa_home_node.db import Database

NOTIF_ALERT = "alert"
NOTIF_CLEARED = "cleared"


class EventStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record_swarm_event(self, event_type: str, node: str | None, text: str, now: datetime) -> None:
        sql = "INSERT INTO swarm_events(event_type, node, text, created_at) VALUES(?, ?, ?, ?)"
        params: Sequence[object] = (event_type, node, text, now.isoformat())
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)

    async def recent_swarm_events(
        self,
        limit: int,
        node: str | None = None,
        event_type: str | None = None,
    ) -> list[dict[str, object]]:
        sql = (
            "SELECT event_type, node, text, created_at FROM swarm_events "
            "WHERE (? IS NULL OR node = ?) AND (? IS NULL OR event_type = ?) "
            "ORDER BY id DESC LIMIT ?"
        )
        return await self._db.fetch_all(sql, (node, node, event_type, event_type, limit))

    async def prune_swarm_events(self, keep_last: int = 500) -> int:
        sql = (
            "DELETE FROM swarm_events WHERE id NOT IN (SELECT id FROM swarm_events ORDER BY id DESC LIMIT ?)"
        )
        async with self._db.transaction() as conn:
            cursor = await conn.execute(sql, (keep_last,))
            return cursor.rowcount

    async def record_notification(
        self, component_id: str, chat_id: int, kind: str, message_id: int | None, sent_at: datetime
    ) -> None:
        sql = (
            "INSERT INTO health_notifications(component_id, chat_id, kind, message_id, sent_at) "
            "VALUES(?, ?, ?, ?, ?) ON CONFLICT(component_id, chat_id, kind) "
            "DO UPDATE SET message_id = excluded.message_id, sent_at = excluded.sent_at"
        )
        params: Sequence[object] = (component_id, chat_id, kind, message_id, sent_at.isoformat())
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)

    async def get_alert_message_id(self, component_id: str, chat_id: int) -> int | None:
        sql = (
            "SELECT message_id FROM health_notifications WHERE component_id = ? AND chat_id = ? AND kind = ?"
        )
        row = await self._db.fetch_one(sql, (component_id, chat_id, NOTIF_ALERT))
        return row["message_id"] if row else None
