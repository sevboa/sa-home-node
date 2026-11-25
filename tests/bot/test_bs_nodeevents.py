from __future__ import annotations

from types import SimpleNamespace

import pytest
from botfix import load_shared
from drivers_w3d import drive_events, drive_render

from sa_home_node.bot import nodeevents
from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.db import open_bot_db
from sa_home_node.bot.spec import EventRoute
from sa_home_node.bot.store_events import EventStore
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.wire import Address, Envelope

DATA = load_shared("nodeevents/events.json")


async def open_ctx(workdir, book, sink):
    db = await open_bot_db(workdir / "events.sqlite", ())
    store = EventStore(db)
    ctx = SimpleNamespace(book=book, notifier=sink, db=db, events=store)

    async def read():
        rows = await store.recent_swarm_events(100)
        await db.close()
        return [[row["event_type"], row["node"], row["text"]] for row in reversed(rows)]

    return ctx, read


KIT = SimpleNamespace(
    ns=SimpleNamespace(Envelope=Envelope, Address=Address),
    Subscription=Subscription,
    Book=SubscriptionBook,
    open=open_ctx,
    route=lambda names, handle: EventRoute(names=names, handle=handle),
    build=lambda ctx, routes: nodeevents.build_node_event_handler(ctx, tuple(routes)),
)


@pytest.mark.parametrize("case", DATA["renders"], ids=lambda c: c["name"])
def test_renders_match_prototype(case):
    assert drive_render(case, nodeevents) == case["expected"]


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_events_match_prototype(case, tmp_path):
    assert await drive_events(case, KIT, tmp_path) == case["expected"]
