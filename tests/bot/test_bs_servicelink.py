from __future__ import annotations

from types import SimpleNamespace

import pytest
from botfix import load
from drivers import drive_servicelink
from netlab import NetLabProxy

from sa_home_node.bot import servicelink
from sa_home_node.bot.servicelink import ServiceLink, ServiceTimeoutError
from sa_home_node.proto.describe import ActionSpec, ServiceDescription, ServiceInfo
from sa_home_node.proto.server import ProtoServer
from sa_home_node.proto.wire import ProtoError, ServiceUnavailableError

DATA = load("servicelink/scenarios.json")

KIT = SimpleNamespace(
    Link=ServiceLink,
    Server=ProtoServer,
    Proxy=NetLabProxy,
    ProtoError=ProtoError,
    Unavailable=ServiceUnavailableError,
    Timeout=ServiceTimeoutError,
    description=lambda ids: ServiceDescription(
        info=ServiceInfo(node="n1", service="stub", version="0.1.0"),
        capabilities=(),
        actions=tuple(ActionSpec(id=i, title=i) for i in ids),
    ),
)


def test_timeout_is_unavailable_subclass_and_delay():
    assert issubclass(ServiceTimeoutError, ServiceUnavailableError)
    assert servicelink.RECONNECT_DELAY_S == DATA["reconnect_delay_s"]


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_link_matches_prototype(case):
    got = await drive_servicelink(case, KIT)
    assert got == {key: case[key] for key in got}
