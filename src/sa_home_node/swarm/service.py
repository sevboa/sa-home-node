from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from sa_home_node import __version__, host
from sa_home_node.proto.actions import ActionTable
from sa_home_node.proto.describe import ServiceDescription, ServiceInfo
from sa_home_node.proto.wire import NODE_SERVICE
from sa_home_node.swarm.router import NodeRouter

log = logging.getLogger(__name__)

StatePart = Callable[[], Any]

CAPABILITIES = ("supervisor", "power")


class NodeService:
    def __init__(
        self,
        node_id: str,
        *,
        kind: str = "",
        version: str = __version__,
        router: NodeRouter | None = None,
        advertise: Callable[[], list[str]] | None = None,
        own_endpoint: str = "",
        wake: Callable[[], dict[str, str] | None] | None = None,
    ) -> None:
        self.node_id = node_id
        self.kind = kind
        self.version = version
        self.router = router
        self.actions = ActionTable()
        self.state_parts: dict[str, StatePart] = {}
        self._started = time.monotonic()
        self._advertise = advertise
        self._own_endpoint = own_endpoint
        self._wake = wake
        self._discovery: Callable[[], dict[str, Any]] | None = None

    def attach_discovery(self, state: Callable[[], dict[str, Any]]) -> None:
        self._discovery = state

    def advertised(self) -> list[str]:
        live = self._advertise() if self._advertise is not None else []
        if live:
            return live
        return [self._own_endpoint] if self._own_endpoint else []

    def describe(self) -> ServiceDescription:
        wake = self._wake() if self._wake is not None else None
        return ServiceDescription(
            info=ServiceInfo(
                node=self.node_id,
                service=NODE_SERVICE,
                version=self.version,
                node_kind=self.kind,
                wake=wake,
                endpoints=tuple(self.advertised()),
                links=self.router.links_table() if self.router is not None else None,
            ),
            capabilities=CAPABILITIES,
            actions=self.actions.specs(),
        )

    async def get_state(self) -> dict[str, Any]:
        state = {
            "node": self.node_id,
            "service": NODE_SERVICE,
            "version": self.version,
            "kind": self.kind,
            "uptime_s": round(time.monotonic() - self._started, 1),
            "system_uptime_s": host.current().system_uptime_s(),
        }
        for name, part in self.state_parts.items():
            try:
                state[name] = part()
            except Exception:
                log.exception("NodeService: вклад состояния %s упал", name)
                state[name] = None
        state["peers"] = self.router.peers_state() if self.router is not None else []
        state["wake"] = self._wake() if self._wake is not None else None
        if self._discovery is not None:
            state["discovery"] = self._discovery()
        return state

    async def run_command(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        return await self.actions.run(action, args)
