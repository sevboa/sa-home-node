from __future__ import annotations

from datetime import datetime

from botfix import load

from sa_home_node.bot.db import open_bot_db
from sa_home_node.bot.store_invites import InviteStore

DATA = load("gate/store.json")


def when(text: str) -> datetime:
    return datetime.fromisoformat(text)


async def apply(store: InviteStore, op: dict):
    kind = op["op"]
    if kind == "create":
        await store.create(op["code"], op["chat"], op["user"], when(op["at"]), when(op["exp"]))
        return None
    if kind == "redeem":
        done = await store.redeem(op["code"], op["chat"], op["user"], op["name"], when(op["at"]))
        return bool(done)
    if kind == "find":
        return await store.find(op["code"])
    if kind == "open":
        return await store.open_codes(when(op["at"]))
    return await store.revoke(op["code"], when(op["at"]))


async def test_operations_match_prototype(tmp_path):
    db = await open_bot_db(tmp_path / "bot.sqlite", ())
    try:
        store = InviteStore(db)
        for op, expected in zip(DATA["ops"], DATA["results"], strict=True):
            assert await apply(store, op) == expected, op
        rows = await db.fetch_all("SELECT * FROM invites ORDER BY code")
        assert [dict(row) for row in rows] == DATA["rows"]
    finally:
        await db.close()
