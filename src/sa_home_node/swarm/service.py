from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sa_home_node import __version__, host
from sa_home_node.proto.actions import ActionTable
from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.describe import ActionParam, ActionSpec, ServiceDescription, ServiceInfo
from sa_home_node.proto.endpoints import merge_endpoints
from sa_home_node.proto.wire import (
    ERR_BAD_REQUEST,
    ERR_UNAVAILABLE,
    ERR_UNKNOWN_DST,
    MSG_COMMAND,
    NODE_SERVICE,
    Address,
    ProtoError,
    make_request,
)
from sa_home_node.swarm.events import ACTION_SWARM_JOIN, EVENT_NODE_JOINED
from sa_home_node.swarm.link import PeerLink
from sa_home_node.swarm.relay import LinkFactory
from sa_home_node.swarm.router import NodeRouter
from sa_home_node.swarm.state import NodeState

log = logging.getLogger(__name__)

StatePart = Callable[[], Any]

CAPABILITIES = ("supervisor", "power")

NODE_ACTION_ORDER = (
    "start",
    "stop",
    "restart",
    "assign",
    "unassign",
    "send_wol",
    "report_ready",
    "trigger_peers",
    "swarm_join",
    "join",
    "restart_node",
    "check_update",
    "update",
    "poweroff",
    "reboot",
    "suspend",
    "close_ssh_sessions",
    "list_instances",
    "get_instance_config",
)

ACTION_TRIGGER_PEERS = "trigger_peers"
TRIGGER_PEERS_TITLE = "📡 Разослать команду службе на всех нодах"
TRIGGER_TIMEOUT_S = 5.0

ACTION_JOIN = "join"
SWARM_JOIN_TITLE = "🤝 Присоединить ноду"
JOIN_TITLE = "🔗 Присоединиться к рою"


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
        state: NodeState | None = None,
        state_path: str | Path | None = None,
        make_peer_link: LinkFactory | None = None,
        emit: Callable[[str, dict[str, Any]], Any] | None = None,
        swarm_token: str = "",
    ) -> None:
        self.node_id = node_id
        self.kind = kind
        self.version = version
        self.router = router
        self.actions = ActionTable(NODE_ACTION_ORDER)
        self.state_parts: dict[str, StatePart] = {}
        self._started = time.monotonic()
        self._advertise = advertise
        self._own_endpoint = own_endpoint
        self._wake = wake
        self._discovery: Callable[[], dict[str, Any]] | None = None
        self.state = state or NodeState()
        self._state_path = Path(state_path) if state_path is not None else None
        self._make_peer_link = make_peer_link or (lambda name, eps: PeerLink(name, eps, token=swarm_token))
        self._emit = emit
        self._swarm_token = swarm_token

        self.actions.add(
            ActionSpec(
                id=ACTION_TRIGGER_PEERS,
                title=TRIGGER_PEERS_TITLE,
                params=(
                    ActionParam(name="service", required=True, title="Служба"),
                    ActionParam(name="action", required=True, title="Действие"),
                ),
            ),
            self.trigger_peers,
        )

        if own_endpoint:
            self.actions.add(
                ActionSpec(
                    id=ACTION_SWARM_JOIN,
                    title=SWARM_JOIN_TITLE,
                    params=(
                        ActionParam(name="node_id", title="Id ноды"),
                        ActionParam(name="endpoint", title="Endpoint"),
                    ),
                ),
                self.swarm_join,
            )
            self.actions.add(
                ActionSpec(
                    id=ACTION_JOIN,
                    title=JOIN_TITLE,
                    params=(ActionParam(name="endpoint", title="Endpoint соседа"),),
                ),
                lambda args: self.join(str(args.get("endpoint", ""))),
            )

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

    def _save_state(self) -> None:
        if self._state_path is not None:
            self.state.save(self._state_path)

    async def trigger_peers(self, args: dict[str, Any]) -> dict[str, Any]:
        service = str(args.get("service", ""))
        if not service:
            raise ProtoError(ERR_BAD_REQUEST, "не передан service")
        action = str(args.get("action", ""))
        if not action:
            raise ProtoError(ERR_BAD_REQUEST, "не передан action")
        call_args = args.get("args") or {}
        if not isinstance(call_args, dict):
            raise ProtoError(ERR_BAD_REQUEST, "args должен быть объектом")
        timeout_s = float(args.get("timeout_s") or TRIGGER_TIMEOUT_S)
        node_ids = [self.node_id]
        if self.router is not None:
            node_ids += [p["id"] for p in self.router.peers_state() if p["alive"]]

        async def dispatch_one(node_id: str) -> tuple[str, str]:
            if self.router is None:
                return node_id, "unreachable"
            env = make_request(
                MSG_COMMAND,
                {"action": action, "args": call_args},
                dst=Address(node=node_id, service=service),
                timeout_s=timeout_s,
            )
            try:
                response = await self.router.route(env)
            except (ConnectionError, OSError, TimeoutError, ProtoError) as exc:
                code = exc.code if isinstance(exc, ProtoError) else None
                return node_id, "skipped" if code == ERR_UNKNOWN_DST else "unreachable"
            if response is None or response.ok is False:
                code = response.error_code() if response is not None else None
                return node_id, "skipped" if code == ERR_UNKNOWN_DST else "unreachable"
            return node_id, "dispatched"

        outcome: dict[str, list[str]] = {"dispatched": [], "skipped": [], "unreachable": []}
        for node_id, status in await asyncio.gather(*(dispatch_one(n) for n in node_ids)):
            outcome[status].append(node_id)
        return outcome

    async def swarm_join(self, args: dict[str, Any]) -> dict[str, Any]:
        caller_id = str(args.get("node_id", ""))
        caller_endpoint = str(args.get("endpoint", ""))
        caller_endpoints = merge_endpoints(caller_endpoint, args.get("endpoints"))
        if not caller_id or not caller_endpoint:
            raise ProtoError(ERR_BAD_REQUEST, "swarm_join требует node_id и endpoint")
        if caller_id == self.node_id:
            raise ProtoError(ERR_BAD_REQUEST, "нода не может присоединиться сама к себе")
        if self.router is not None:
            existing = self.router.peers.get(caller_id)
            if existing is None or existing.endpoints != caller_endpoints:
                if existing is not None:
                    await self.router.remove_peer(caller_id)
                link = self._make_peer_link(caller_id, caller_endpoints)
                await self.router.add_peer(link)
            self.state.remember_peer(caller_id, caller_endpoint, caller_endpoints)
            self._save_state()
        if self._emit is not None:
            await self._emit(
                EVENT_NODE_JOINED,
                {"node_id": caller_id, "endpoint": caller_endpoint, "endpoints": caller_endpoints},
            )
        advertised = self.advertised()
        peers = [
            {
                "id": self.node_id,
                "endpoint": advertised[0] if advertised else self._own_endpoint,
                "endpoints": advertised,
                "alive": True,
            }
        ]
        if self.router is not None:
            peers += self.router.peers_state()
        return {"peers": peers}

    async def join(self, endpoint: str) -> dict[str, Any]:
        if not endpoint:
            raise ProtoError(ERR_BAD_REQUEST, "join требует endpoint")
        advertised = self.advertised()
        client = ProtoClient(endpoint, token=self._swarm_token)
        try:
            await client.connect()
            result = await client.command(
                ACTION_SWARM_JOIN,
                {
                    "node_id": self.node_id,
                    "endpoint": advertised[0] if advertised else self._own_endpoint,
                    "endpoints": advertised,
                },
            )
        except (ConnectionError, OSError, TimeoutError, ProtoError) as exc:
            raise ProtoError(ERR_UNAVAILABLE, f"сосед {endpoint} недоступен: {exc}") from exc
        finally:
            await client.close()
        added = []
        if self.router is not None:
            for peer in result.get("peers", []):
                pid = peer.get("id")
                peer_endpoint = peer.get("endpoint")
                if not pid or not peer_endpoint or pid == self.node_id or pid in self.router.peers:
                    continue
                peer_endpoints = merge_endpoints(peer_endpoint, peer.get("endpoints"))
                link = self._make_peer_link(pid, peer_endpoints)
                await self.router.add_peer(link)
                self.state.remember_peer(pid, peer_endpoint, peer_endpoints)
                self._save_state()
                added.append(pid)
        return {"joined_via": endpoint, "peers_added": added}
