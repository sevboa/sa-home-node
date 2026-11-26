from __future__ import annotations

from datetime import datetime

import pytest
from botfix import load

from sa_home_node.bot import store_outbox
from sa_home_node.bot.db import open_bot_db
from sa_home_node.bot.store_outbox import OutboxStore

DATA = load("outbox/store.json")


def when(text: str) -> datetime:
    return datetime.fromisoformat(text)


@pytest.mark.parametrize("name", sorted(DATA["kinds"]))
def test_kind_constants(name):
    assert getattr(store_outbox, f"OUTBOX_KIND_{name}") == DATA["kinds"][name]


async def apply(store: OutboxStore, op: dict):
    kind = op["op"]
    if kind == "enqueue":
        return await store.enqueue(
            op["chat_id"],
            op["kind"],
            op["payload"],
            now=when(op["at"]),
            dedup_key=op.get("dedup"),
            message_thread_id=op.get("thread"),
        )
    if kind == "due":
        rows = await store.due(when(op["at"]), *([op["limit"]] if "limit" in op else []))
        return [{**row, "created_at": row["created_at"].isoformat()} for row in rows]
    if kind == "bump":
        await store.bump_attempts(op["id"])
        return None
    if kind == "delete":
        await store.delete(op["id"])
        return None
    return await store.expire(when(op["at"]))


async def test_operations_match_prototype(tmp_path):
    db = await open_bot_db(tmp_path / "bot.sqlite", ())
    try:
        store = OutboxStore(db)
        for op, expected in zip(DATA["ops"], DATA["results"], strict=True):
            assert await apply(store, op) == expected, op
        rows = await db.fetch_all("SELECT * FROM outbox ORDER BY id")
        assert [dict(row) for row in rows] == DATA["rows"]
    finally:
        await db.close()
