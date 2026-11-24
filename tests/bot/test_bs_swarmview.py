from __future__ import annotations

from types import SimpleNamespace

import pytest
from drivers_w2b import import_optional, node_command_port
from drivers_w3c import drive_swarm, load_shared

from sa_home_node.bot import swarmview
from sa_home_node.bot.db import AppState, open_bot_db
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.describe import ActionSpec, ServiceDescription
from sa_home_node.proto.wire import Address, ProtoError, ServiceUnavailableError
from sa_home_node.wake.peers import NodeReport

DATA = load_shared("swarm/swarm.json")
NODELINKS = import_optional("nodelinks")
NS = SimpleNamespace(
    ActionSpec=ActionSpec,
    ServiceDescription=ServiceDescription,
    ProtoError=ProtoError,
    ServiceUnavailableError=ServiceUnavailableError,
    Address=Address,
    Subscription=Subscription,
)


async def open_state(path):
    db = await open_bot_db(path, ())
    return AppState(db), db.close


KIT = SimpleNamespace(
    mod=swarmview,
    ns=NS,
    NodeReport=NodeReport,
    open_state=open_state,
    state_set=lambda state, key, value: state.set(key, value),
    state_get=lambda state, key: state.get(key),
    patches=[] if NODELINKS is None else [(NODELINKS, "node_command", node_command_port)],
)


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_swarm_view_matches_prototype(case, tmp_path):
    assert await drive_swarm(case, KIT, tmp_path) == case["expected"]
