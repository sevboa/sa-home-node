from __future__ import annotations

from types import SimpleNamespace

import pytest
from botfix import load
from drivers_w2b import drive_nodeview, import_optional

from sa_home_node.bot import nodeview
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.describe import ActionSpec, ServiceDescription
from sa_home_node.proto.wire import Address, ProtoError, ServiceUnavailableError

DATA = load("swarm/node-views.json")
NS = SimpleNamespace(
    ActionSpec=ActionSpec,
    ServiceDescription=ServiceDescription,
    ProtoError=ProtoError,
    ServiceUnavailableError=ServiceUnavailableError,
    Address=Address,
    Subscription=Subscription,
    statusview=import_optional("statusview"),
    nodelinks=import_optional("nodelinks"),
    stub_keyboard=True,
)


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
def test_node_views_match_prototype(case):
    assert drive_nodeview(case, nodeview, NS) == case["expected"]
