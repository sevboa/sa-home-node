from __future__ import annotations

from types import SimpleNamespace

import pytest
from drivers_w3c import drive_alerts, load_shared, sub_factory
from sa_home_node.bot.alerts import AlertDispatcher, build_event_handler

from sa_home_node.bot import monitor_parse
from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.db import open_bot_db
from sa_home_node.bot.store_events import EventStore
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.wire import Envelope

DATA = load_shared("alerts/dispatch.json")


async def open_events(path):
    db = await open_bot_db(path, ())
    return EventStore(db), db.close


KIT = SimpleNamespace(
    prepare=lambda path, components: None,
    open_events=open_events,
    book=SubscriptionBook,
    sub=sub_factory(Subscription),
    Dispatcher=AlertDispatcher,
    build_event_handler=build_event_handler,
    parse=monitor_parse,
    envelope=lambda payload: Envelope(type="event", id="e1", payload=payload),
)


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_alerts_match_prototype(case, tmp_path):
    got = await drive_alerts(case, KIT, tmp_path)
    assert got == {key: case[key] for key in got}
