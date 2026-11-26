from __future__ import annotations

from collections.abc import Iterator

import pytest

from sa_home_node.bot import (
    handlers_basic,
    handlers_invites,
    handlers_node,
    handlers_status,
    handlers_wake,
    nodelinks,
    swarmpanel,
)

CORE_ROUTER_MODULES = (
    handlers_invites,
    handlers_wake,
    swarmpanel,
    handlers_status,
    nodelinks,
    handlers_node,
    handlers_basic,
)


def _detach() -> None:
    for module in CORE_ROUTER_MODULES:
        module.router._parent_router = None


@pytest.fixture
def detached_routers() -> Iterator[None]:
    _detach()
    yield
    _detach()
