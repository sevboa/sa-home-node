from __future__ import annotations

import asyncio
import contextlib
import logging

from sa_home_node import __version__
from sa_home_node.config import NodeConfig
from sa_home_node.proto.server import ProtoServer
from sa_home_node.proto.wire import (
    MSG_COMMAND,
    MSG_DESCRIBE,
    MSG_GET_STATE,
    NODE_SERVICE,
    Address,
    Envelope,
    ProtoError,
    make_event,
    make_request,
)
from sa_home_node.swarm.config import SwarmSection, swarm_section
from sa_home_node.swarm.discovery import SwarmDiscovery
from sa_home_node.swarm.events import EVENT_NODE_LEAVING
from sa_home_node.swarm.link import PeerLink
from sa_home_node.swarm.presence import PresenceWatcher
from sa_home_node.swarm.relay import EventHandler, EventRelay, announce_version
from sa_home_node.swarm.router import NodeRouter
from sa_home_node.swarm.service import NodeService
from sa_home_node.swarm.state import NodeState

log = logging.getLogger(__name__)

LOCAL_HEARTBEAT_TIMEOUT_S = 20.0
COMMAND_TIMEOUT_S = 10.0


class SwarmApi:
    def __init__(self, swarm: Swarm) -> None:
        self._swarm = swarm

    @property
    def node_id(self) -> str:
        return self._swarm.node_id

    async def emit(self, event: str, data: dict, *, service: str = NODE_SERVICE) -> None:
        await self._swarm.emit(event, data, service=service)

    def subscribe(self, handler: EventHandler) -> None:
        self._swarm.subscribe(handler)

    async def _call(
        self, type_: str, payload: dict, dst: str, service: str, timeout_s: float
    ) -> dict:
        env = make_request(
            type_, payload, dst=Address(node=dst, service=service), timeout_s=timeout_s
        )
        response = await self._swarm.request(env)
        if response.ok is not True:
            raise ProtoError(response.error_code() or "unknown", response.error_message())
        return response.payload

    async def command(
        self,
        dst: str,
        action: str,
        args: dict | None = None,
        *,
        service: str = NODE_SERVICE,
        timeout_s: float = COMMAND_TIMEOUT_S,
    ) -> dict:
        return await self._call(
            MSG_COMMAND, {"action": action, "args": args or {}}, dst, service, timeout_s
        )

    async def get_state(self, dst: str, *, service: str = NODE_SERVICE) -> dict:
        return await self._call(MSG_GET_STATE, {}, dst, service, COMMAND_TIMEOUT_S)

    async def describe(self, dst: str, *, service: str = NODE_SERVICE) -> dict:
        return await self._call(MSG_DESCRIBE, {}, dst, service, COMMAND_TIMEOUT_S)


def make_link_factories(
    swarm: SwarmSection,
    node_id: str,
    on_peer_event,
    on_local_event,
    on_endpoints=None,
):
    def make_peer_link(peer_id: str, endpoint) -> PeerLink:
        return PeerLink(
            peer_id,
            endpoint,
            token=swarm.token,
            on_event=on_peer_event,
            on_endpoints=on_endpoints,
            self_node=node_id,
        )

    def make_local_link(service: str, endpoint: str) -> PeerLink:
        return PeerLink(
            service,
            endpoint,
            token=swarm.token,
            on_event=on_local_event,
            heartbeat_timeout=LOCAL_HEARTBEAT_TIMEOUT_S,
        )

    return make_peer_link, make_local_link


def peer_candidates(nodes, extra) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for cfg in (*extra, *nodes):
        known = out.setdefault(cfg.id, [])
        for value in (cfg.endpoint, *cfg.endpoints):
            if value and value not in known:
                known.append(value)
    return out


def peer_kinds(nodes, extra) -> dict[str, str]:
    out: dict[str, str] = {}
    for cfg in (*extra, *nodes):
        if cfg.kind and cfg.id not in out:
            out[cfg.id] = cfg.kind
    return out


class Swarm:
    def __init__(self, cfg: NodeConfig, state: NodeState, version: str = __version__) -> None:
        self.node_id = cfg.node.id
        self.kind = cfg.node.kind
        self.version = version
        self.swarm = swarm_section(cfg)
        self.state = state
        self.state_path = cfg.node.state_path
        self.router = NodeRouter(self.node_id)
        self.presence: PresenceWatcher | None = None
        self.discovery: SwarmDiscovery | None = None
        self._announce_task: asyncio.Task | None = None
        self._started = False

        async def on_peer_event(env: Envelope) -> None:
            await self.relay.on_event(env)

        async def on_local_event(env: Envelope) -> None:
            await self.relay.on_event(env, local=True)

        def on_peer_endpoints(peer_id: str, endpoints: list[str]) -> None:
            if not endpoints:
                return
            self.state.remember_peer(peer_id, endpoints[0], endpoints)
            self.state.save(self.state_path)

        def on_peer_connect(peer_node: str) -> None:
            link = self.router.peers.get(peer_node)
            if link is not None:
                link.reconnect_now("сосед подключился заново")

        make_peer_link, make_local_link = make_link_factories(
            self.swarm, self.node_id, on_peer_event, on_local_event, on_peer_endpoints
        )
        self._make_local_link = make_local_link

        kinds = peer_kinds(self.swarm.nodes, self.state.peers)
        for peer_id, endpoints in peer_candidates(self.swarm.nodes, self.state.peers).items():
            if peer_id == self.node_id:
                continue
            if not endpoints:
                log.warning("swarm: у соседа %s нет ни одного адреса — пропускаю", peer_id)
                continue
            link = make_peer_link(peer_id, endpoints)
            kind = kinds.get(peer_id)
            if kind and not link.node_kind:
                link.node_kind = kind
            self.router.peers[peer_id] = link

        node_service = NodeService(
            self.node_id,
            kind=self.kind,
            version=self.version,
            router=self.router,
            advertise=lambda: self.server.advertised_endpoints(),
            own_endpoint=cfg.node.listen[0] if cfg.node.listen else "",
            state=self.state,
            state_path=self.state_path,
            make_peer_link=make_peer_link,
            emit=self.emit,
            swarm_token=self.swarm.token,
        )
        self.node_service = node_service
        self.server = ProtoServer(
            [cfg.node.socket, *cfg.node.listen],
            node_service,
            token=self.swarm.token,
            router=self.router.route,
            on_peer_connect=on_peer_connect,
        )
        self.relay = EventRelay(
            self.node_id,
            router=self.router,
            state=self.state,
            state_path=self.state_path,
            make_peer_link=make_peer_link,
            broadcast=self.server.broadcast_envelope,
        )

    async def start(self) -> None:
        await self.server.start()
        self._started = True
        self._announce_task = asyncio.create_task(
            announce_version(
                self.state, self.state_path, self.server.broadcast_event, version=self.version
            ),
            name="announce-version",
        )
        for link in self.router.peers.values():
            await link.start()
        self.presence = PresenceWatcher(
            self.node_id,
            self.router,
            emit=self.emit,
            state=self.state,
            state_path=self.state_path,
            down_after_s=self.swarm.node_down_alert_after_s,
        )
        await self.presence.start()
        self.discovery = SwarmDiscovery(
            self.node_id,
            self.router,
            token=self.swarm.token,
            join=self.node_service.join,
            advertise=self.server.advertised_endpoints,
            cfg=self.swarm.discovery,
            node_kind=self.kind,
        )
        self.node_service.attach_discovery(self.discovery.state)
        await self.discovery.start()
        if self.swarm.join and not self.state.peers:
            try:
                result = await self.node_service.join(self.swarm.join)
                log.info(
                    "swarm.join: присоединились через %s, новых пиров: %s",
                    self.swarm.join,
                    result["peers_added"],
                )
            except ProtoError as exc:
                log.warning(
                    "swarm.join: сосед %s недоступен (%s) — присоединюсь позже", self.swarm.join, exc
                )

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        await self.emit(EVENT_NODE_LEAVING, {"node": self.node_id, "kind": self.kind})
        if self._announce_task is not None:
            self._announce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._announce_task
            self._announce_task = None
        if self.presence is not None:
            await self.presence.stop()
        if self.discovery is not None:
            await self.discovery.stop()
        for link in self.router.peers.values():
            await link.stop()
        await self.server.stop()

    async def emit(self, event: str, data: dict, *, service: str = NODE_SERVICE) -> None:
        env = make_event(event, data, src=Address(node=self.node_id, service=service))
        await self.relay.on_event(env, local=True)

    def subscribe(self, handler: EventHandler) -> None:
        self.relay.handlers.append(handler)

    async def request(self, env: Envelope) -> Envelope:
        response = await self.router.route(env)
        if response is None:
            response = await self.server.dispatch(env)
        return response
