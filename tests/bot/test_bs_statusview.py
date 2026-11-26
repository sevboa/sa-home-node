from __future__ import annotations

from types import SimpleNamespace

import pytest
from botfix import load_shared
from drivers_w3c import drive_statusview

from sa_home_node.bot import statusview
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.describe import ActionSpec, ServiceDescription
from sa_home_node.proto.wire import Address, ProtoError, ServiceUnavailableError

DATA = load_shared("status/views.json")
KIT = SimpleNamespace(
    mod=statusview,
    ns=SimpleNamespace(
        ActionSpec=ActionSpec,
        ServiceDescription=ServiceDescription,
        ProtoError=ProtoError,
        ServiceUnavailableError=ServiceUnavailableError,
        Address=Address,
        Subscription=Subscription,
    ),
)


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_status_views_match_prototype(case):
    assert await drive_statusview(case, KIT) == case["expected"]
