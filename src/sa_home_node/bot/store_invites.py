from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sa_home_node.db import Database

log = logging.getLogger(__name__)


class InviteStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        code: str,
        issued_by_chat_id: int,
        issued_by_user_id: int | None,
        now: datetime,
        expires_at: datetime,
    ) -> None:
        sql = (
            "INSERT INTO invites(code, issued_by_chat_id, issued_by_user_id, created_at, expires_at) "
            "VALUES(?, ?, ?, ?, ?)"
        )
        params = (
            code,
            issued_by_chat_id,
            issued_by_user_id,
            now.isoformat(),
            expires_at.isoformat(),
        )
        await self._write(sql, params)
        log.info("Код приглашения выдан чатом %s, истекает %s", issued_by_chat_id, expires_at.isoformat())

    async def redeem(
        self, code: str, chat_id: int, user_id: int | None, user_name: str | None, now: datetime
    ) -> bool:
        sql = (
            "UPDATE invites SET redeemed_at = ?, redeemed_chat_id = ?, redeemed_user_id = ?, redeemed_user_name = ? "
            "WHERE code = ? AND redeemed_at IS NULL AND revoked_at IS NULL AND expires_at > ?"
        )
        rowcount = await self._write(
            sql, (now.isoformat(), chat_id, user_id, user_name, code, now.isoformat())
        )
        if rowcount:
            log.info("Код приглашения погашен чатом %s", chat_id)
        return rowcount > 0

    async def find(self, code: str) -> dict[str, Any] | None:
        sql = "SELECT * FROM invites WHERE code = ?"
        return await self._db.fetch_one(sql, (code,))

    async def open_codes(self, now: datetime) -> list[dict[str, Any]]:
        sql = (
            "SELECT * FROM invites "
            "WHERE redeemed_at IS NULL AND revoked_at IS NULL AND expires_at > ? "
            "ORDER BY created_at"
        )
        return await self._db.fetch_all(sql, (now.isoformat(),))

    async def revoke(self, code: str, at: datetime) -> bool:
        sql = (
            "UPDATE invites SET revoked_at = ? WHERE code = ? AND redeemed_at IS NULL AND revoked_at IS NULL"
        )
        rowcount = await self._write(sql, (at.isoformat(), code))
        if rowcount:
            log.info("Код приглашения отозван")
        return rowcount > 0

    async def _write(self, sql: str, params: Sequence[object]) -> int:
        async with self._db.transaction() as conn:
            cursor = await conn.execute(sql, params)
            return cursor.rowcount
