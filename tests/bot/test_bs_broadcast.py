from __future__ import annotations

import pytest
from botfix import load
from drivers import drive_broadcast

from sa_home_node.bot import broadcast
from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.subs import Subscription

DATA = load("subs/broadcast.json")
FUNCS = {
    name: getattr(broadcast, name)
    for name in ("broadcast", "admin_subs", "notify_admins", "broadcast_system", "broadcast_all")
}


def make_book() -> SubscriptionBook:
    return SubscriptionBook(
        [
            Subscription(
                name=d["name"],
                chat_id=d["chat_id"],
                event_types=frozenset(d["event_types"]),
                except_event_types=frozenset(d["except_event_types"]),
                allowed_commands=frozenset(d["allowed_commands"]),
                broken=d["broken"],
                source=d["source"],
            )
            for d in DATA["subs"]
        ]
    )


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_broadcast_matches_prototype(case):
    got = await drive_broadcast(case, FUNCS, make_book())
    assert got == {key: case[key] for key in got}
