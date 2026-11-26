from __future__ import annotations

from types import SimpleNamespace

import pytest
from botfix import load
from drivers_w2a import drive_flusher, drive_outbox

from sa_home_node.bot.db import open_bot_db
from sa_home_node.bot.notifier import Notifier
from sa_home_node.bot.outboxflush import OutboxFlusher, flush_outbox
from sa_home_node.bot.store_outbox import OutboxStore

DATA = load("outbox/flush.json")


async def open_outbox(path):
    db = await open_bot_db(path, ())
    return OutboxStore(db), db.close


async def enqueue(outbox, chat_id, kind, payload, now, thread):
    await outbox.enqueue(chat_id, kind, payload, now=now, message_thread_id=thread)


def break_due(outbox):
    original = outbox.due

    async def boom(*args, **kwargs):
        raise RuntimeError("db")

    outbox.due = boom
    return lambda: setattr(outbox, "due", original)


KIT = SimpleNamespace(
    open=open_outbox,
    enqueue=enqueue,
    notifier=Notifier,
    flush=flush_outbox,
    Flusher=OutboxFlusher,
    break_due=break_due,
)


@pytest.mark.parametrize("case", DATA["flush"], ids=lambda c: c["name"])
async def test_flush_matches_prototype(case, tmp_path):
    got = await drive_outbox(case, KIT, tmp_path)
    assert got == {key: case[key] for key in got}


@pytest.mark.parametrize("case", DATA["flusher"], ids=lambda c: c["name"])
async def test_flusher_matches_prototype(case, tmp_path):
    got = await drive_flusher(case, KIT, tmp_path)
    assert got == {key: case[key] for key in got}
