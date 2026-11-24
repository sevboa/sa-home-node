from __future__ import annotations

from types import SimpleNamespace

import pytest
from drivers_w3c import drive_nodelinks, load_shared

from sa_home_node.bot import nodelinks, nodeview
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.describe import ActionSpec, ServiceDescription
from sa_home_node.proto.wire import Address, ProtoError, ServiceUnavailableError

DATA = load_shared("swarm/links.json")
NS = SimpleNamespace(
    ActionSpec=ActionSpec,
    ServiceDescription=ServiceDescription,
    ProtoError=ProtoError,
    ServiceUnavailableError=ServiceUnavailableError,
    Address=Address,
    Subscription=Subscription,
)
KIT = SimpleNamespace(
    ns=NS,
    routers=[nodelinks.router],
    data=lambda ctx: {"node_link": ctx.link, "subscription": ctx.sub},
    targets={
        "node_card": (nodeview, "build_node_card_view"),
        "service_card": (nodeview, "build_service_card_view"),
    },
)


def test_prefixes():
    assert nodelinks.NODE_PREFIX == DATA["constants"]["node_prefix"]
    assert nodelinks.SVC_PREFIX == DATA["constants"]["svc_prefix"]


@pytest.mark.parametrize("pair", DATA["normalize"], ids=lambda p: repr(p[0]))
def test_normalize(pair):
    assert nodelinks.normalize(pair[0]) == pair[1]


@pytest.mark.parametrize("row", DATA["node_command"], ids=lambda r: repr(r[0]))
def test_node_command(row):
    assert nodelinks.node_command(row[0]) == row[1]


@pytest.mark.parametrize("row", DATA["svc_command"], ids=lambda r: repr(r[:2]))
def test_svc_command(row):
    assert nodelinks.svc_command(row[0], row[1]) == row[2]


@pytest.mark.parametrize("row", DATA["candidates"], ids=lambda r: repr(r[0]))
def test_resolve_svc_candidates(row):
    assert [list(c) for c in nodelinks.resolve_svc_candidates(row[0], row[1])] == row[2]


@pytest.mark.parametrize("row", DATA["match"], ids=lambda r: repr(r[0]))
def test_match_name(row):
    assert nodelinks.match_name(row[0], row[1]) == row[2]


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_node_links_match_prototype(case):
    got = await drive_nodelinks(case, KIT)
    assert got == {key: case[key] for key in got}
