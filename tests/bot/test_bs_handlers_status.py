from __future__ import annotations

from types import SimpleNamespace

import pytest
from botfix import load_shared
from drivers_w2b import import_optional
from drivers_w3c import drive_status_handlers

from sa_home_node.bot import handlers_status, nodeview, statusview
from sa_home_node.bot.commands import Registry
from sa_home_node.bot.db import AppState, open_bot_db
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.describe import ActionSpec, ServiceDescription
from sa_home_node.proto.wire import Address, ProtoError, ServiceUnavailableError

DATA = load_shared("status/handlers.json")
SWARMVIEW = import_optional("swarmview")
NS = SimpleNamespace(
    ActionSpec=ActionSpec,
    ServiceDescription=ServiceDescription,
    ProtoError=ProtoError,
    ServiceUnavailableError=ServiceUnavailableError,
    Address=Address,
    Subscription=Subscription,
)
REGISTRY = Registry()
TARGETS = {
    "node_card": (nodeview, "build_node_card_view"),
    "service_card": (nodeview, "build_service_card_view"),
    "full": (statusview, "build_full_text"),
    "stats": (statusview, "build_stats_text"),
    "downtime": (statusview, "build_downtime_page"),
}
if SWARMVIEW is not None:
    TARGETS["swarm"] = (SWARMVIEW, "build_swarm_view")


async def open_state(path):
    db = await open_bot_db(path, ())
    return AppState(db), db.close


def data(ctx):
    return {
        "node_link": ctx.link,
        "subscription": ctx.sub,
        "registry": REGISTRY,
        "wake": ctx.wake,
        "app_state": ctx.state,
    }


KIT = SimpleNamespace(
    open_state=open_state,
    state_set=lambda state, key, value: state.set(key, value),
    state_get=lambda state, key: state.get(key),
    ns=NS,
    routers=[handlers_status.router],
    data=data,
    targets=TARGETS,
)


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_status_handlers_match_prototype(case, tmp_path):
    got = await drive_status_handlers(case, KIT, tmp_path)
    assert got == {key: case[key] for key in got}
