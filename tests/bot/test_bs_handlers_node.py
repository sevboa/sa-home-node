from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from botfix import load_shared
from drivers_w2b import import_optional
from drivers_w3d import drive_node

from sa_home_node.bot import handlers_node
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.describe import ActionSpec, ServiceDescription
from sa_home_node.proto.wire import Address, ProtoError, ServiceUnavailableError

DATA = load_shared("handlers/node.json")
NS = SimpleNamespace(
    ActionSpec=ActionSpec,
    ServiceDescription=ServiceDescription,
    ProtoError=ProtoError,
    ServiceUnavailableError=ServiceUnavailableError,
    Address=Address,
)
KIT = SimpleNamespace(
    ns=NS,
    Subscription=Subscription,
    router=handlers_node.router,
    data=lambda case, **kw: kw,
    setup=nullcontext,
    statusview=import_optional("statusview"),
    nodelinks=import_optional("nodelinks"),
)


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_node_actions_match_prototype(case):
    assert await drive_node(case, KIT) == case["expected"]
