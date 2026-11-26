from __future__ import annotations

from types import SimpleNamespace

import pytest
from botfix import load_shared
from drivers_w2a import local_zone
from drivers_w3c import drive_invites, patched, sub_factory

from sa_home_node.bot import handlers_invites, menu
from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.commands import Registry
from sa_home_node.bot.config import InvitesConfig
from sa_home_node.bot.db import open_bot_db
from sa_home_node.bot.gate import Gatekeeper
from sa_home_node.bot.guestfile import GuestStore
from sa_home_node.bot.rights import GuestRight, RightGroup, RightsCatalog
from sa_home_node.bot.store_invites import InviteStore
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.describe import ActionSpec, ServiceDescription
from sa_home_node.proto.wire import Address, ProtoError, ServiceUnavailableError

DATA = load_shared("gate/handlers.json")
NS = SimpleNamespace(
    ActionSpec=ActionSpec,
    ServiceDescription=ServiceDescription,
    ProtoError=ProtoError,
    ServiceUnavailableError=ServiceUnavailableError,
    Address=Address,
)
CATALOGS = {
    name: RightsCatalog(
        [
            RightGroup(
                g["key"],
                g["title"],
                tuple(GuestRight(m["right"], m["title"], m["hint"]) for m in g["members"]),
                g["fine"],
            )
            for g in groups
        ]
    )
    for name, groups in DATA["catalogs"].items()
}
REGISTRY = Registry()


async def open_invites(path):
    db = await open_bot_db(path, ())
    return InviteStore(db), db.close


def patch_menu(record, usage):
    return patched([(menu, "refresh_chat_menu", record), (handlers_invites, "refresh_chat_menu", record)])


def data(ctx):
    return {
        "gate": ctx.gate,
        "book": ctx.book,
        "guest_store": ctx.guest_store,
        "registry": REGISTRY,
        "catalog": ctx.catalog,
        "bot_username": ctx.username,
        "notifier": ctx.notifier,
        "apps_link": ctx.apps,
        "guest_usage": ctx.usage,
        "on_admitted": ctx.hook,
        "admission": ctx.admission,
        "about_name": "sa-home-node",
    }


KIT = SimpleNamespace(
    open_invites=open_invites,
    book=SubscriptionBook,
    sub=sub_factory(Subscription),
    guest_store=GuestStore,
    Gatekeeper=Gatekeeper,
    cfg=lambda spec: InvitesConfig(**spec),
    catalog=lambda name: CATALOGS[name],
    ns=NS,
    routers=[handlers_invites.router],
    patch_menu=patch_menu,
    data=data,
)


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_invite_handlers_match_prototype(case, tmp_path):
    with local_zone():
        got = await drive_invites(case, KIT, tmp_path)
    assert got == {key: case[key] for key in got}
