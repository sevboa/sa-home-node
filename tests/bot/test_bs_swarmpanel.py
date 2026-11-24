from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from drivers_w2b import import_optional
from drivers_w3d import drive_panel, drive_panel_view, drive_summary, load_shared

from sa_home_node.bot import swarmpanel
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.describe import ActionSpec, ServiceDescription
from sa_home_node.proto.wire import Address, ProtoError, ServiceUnavailableError
from sa_home_node.wake.peers import NodeReport

DATA = load_shared("swarm/panel.json")
NS = SimpleNamespace(
    ActionSpec=ActionSpec,
    ServiceDescription=ServiceDescription,
    ProtoError=ProtoError,
    ServiceUnavailableError=ServiceUnavailableError,
    Address=Address,
    NodeReport=NodeReport,
)
KIT = SimpleNamespace(
    ns=NS,
    Subscription=Subscription,
    mod=swarmpanel,
    router=swarmpanel.router,
    data=lambda case, **kw: kw,
    setup=nullcontext,
    statusview=import_optional("statusview"),
    nodelinks=import_optional("nodelinks"),
    summary=swarmpanel.check_updates_summary,
)


def test_page_sizes():
    assert {"nodes": swarmpanel.NODES_PAGE_SIZE, "skills": swarmpanel.SKILLS_PAGE_SIZE} == DATA["sizes"]


@pytest.mark.parametrize("case", DATA["views"], ids=lambda c: c["name"])
def test_views_match_prototype(case):
    assert drive_panel_view(case, KIT) == case["expected"]


@pytest.mark.parametrize("case", DATA["handlers"], ids=lambda c: c["name"])
async def test_screens_match_prototype(case):
    assert await drive_panel(case, KIT) == case["expected"]


@pytest.mark.parametrize("case", DATA["summaries"], ids=lambda c: c["name"])
async def test_update_summary_matches_prototype(case):
    assert await drive_summary(case, KIT) == case["expected"]
