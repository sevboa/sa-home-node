from __future__ import annotations

from datetime import datetime

from botfix import load

from sa_home_node.bot import store_events
from sa_home_node.bot.db import open_bot_db
from sa_home_node.bot.store_events import EventStore

DATA = load("db/events.json")


def when(text: str) -> datetime:
    return datetime.fromisoformat(text)


def test_notification_kinds():
    assert (store_events.NOTIF_ALERT, store_events.NOTIF_CLEARED) == (
        DATA["consts"]["NOTIF_ALERT"],
        DATA["consts"]["NOTIF_CLEARED"],
    )


async def apply(store: EventStore, op: dict):
    kind = op["op"]
    if kind == "event":
        await store.record_swarm_event(op["type"], op["node"], op["text"], when(op["at"]))
        return None
    if kind == "recent":
        return await store.recent_swarm_events(op["limit"], node=op.get("node"), event_type=op.get("type"))
    if kind == "prune":
        return await store.prune_swarm_events(op["keep"])
    if kind == "notify":
        await store.record_notification(
            op["component"], op["chat"], op["kind"], op["message"], when(op["at"])
        )
        return None
    return await store.get_alert_message_id(op["component"], op["chat"])


async def test_operations_match_prototype(tmp_path):
    db = await open_bot_db(tmp_path / "bot.sqlite", ())
    try:
        store = EventStore(db)
        for op, expected in zip(DATA["ops"], DATA["results"], strict=True):
            assert await apply(store, op) == expected, op
        rows = await db.fetch_all("SELECT * FROM health_notifications ORDER BY component_id, chat_id, kind")
        assert [dict(row) for row in rows] == DATA["notifications"]
    finally:
        await db.close()


async def test_prune_default_keeps_five_hundred(tmp_path):
    db = await open_bot_db(tmp_path / "bot.sqlite", ())
    try:
        store = EventStore(db)
        at = when("2026-01-01T11:00:00+00:00")
        for number in range(505):
            await store.record_swarm_event("bulk", None, f"b{number}", at)
        bulk = DATA["bulk"]
        assert await store.prune_swarm_events() == bulk["deleted"]
        rows = await store.recent_swarm_events(1000)
        assert (len(rows), rows[0]["text"], rows[-1]["text"]) == (
            bulk["remaining"],
            bulk["newest"],
            bulk["oldest"],
        )
    finally:
        await db.close()
