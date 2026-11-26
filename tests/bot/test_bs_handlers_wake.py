from __future__ import annotations

from types import SimpleNamespace

import pytest
from botfix import load_shared
from drivers_w3d import drive_wake, drive_wakestate

from sa_home_node import wol
from sa_home_node.bot import handlers_wake, wakestate
from sa_home_node.proto.describe import ActionSpec, ServiceDescription
from sa_home_node.proto.wire import Address, ProtoError, ServiceUnavailableError

DATA = load_shared("handlers/wake.json")
NS = SimpleNamespace(
    ActionSpec=ActionSpec,
    ServiceDescription=ServiceDescription,
    ProtoError=ProtoError,
    ServiceUnavailableError=ServiceUnavailableError,
    Address=Address,
)
KIT = SimpleNamespace(
    ns=NS,
    wol=wol,
    router=handlers_wake.router,
    data=lambda case, **kw: kw,
)


async def put(state, node, wake):
    await wakestate.StateWakeCache(state).put(node, wake)


async def get(state, node):
    return await wakestate.StateWakeCache(state).get(node)


STATE_KIT = SimpleNamespace(put=put, get=get)


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_wake_matches_prototype(case):
    assert await drive_wake(case, KIT) == case["expected"]


@pytest.mark.parametrize("case", DATA["wakestate"], ids=lambda c: c["name"])
async def test_wake_cache_matches_prototype(case):
    assert await drive_wakestate(case, STATE_KIT) == case["expected"]
