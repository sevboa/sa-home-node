from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from sa_home_node import __version__
from sa_home_node.proto.endpoints import merge_endpoints
from sa_home_node.proto.wire import MSG_EVENT, Envelope
from sa_home_node.swarm.link import PeerLink
from sa_home_node.swarm.router import NodeRouter
from sa_home_node.swarm.state import NodeState

log = logging.getLogger(__name__)

SEEN_EVENT_IDS_CAPACITY = 2048
MAX_EVENT_HOPS = 8

EventHandler = Callable[[Envelope], Awaitable[None]]
LinkFactory = Callable[[str, Sequence[str]], PeerLink]


class SeenEvents:
    def __init__(self, maxsize: int = SEEN_EVENT_IDS_CAPACITY) -> None:
        self._maxsize = maxsize
        self._ids: dict[str, None] = {}

    def seen(self, event_id: str) -> bool:
        if event_id in self._ids:
            return True
        self._ids[event_id] = None
        if len(self._ids) > self._maxsize:
            self._ids.pop(next(iter(self._ids)))
        return False


class EventRelay:
    def __init__(
        self,
        node_id: str,
        *,
        router: NodeRouter,
        state: NodeState,
        state_path: str | Path,
        make_peer_link: LinkFactory,
        broadcast: Callable[[Envelope], Awaitable[int]] | None = None,
        handlers: Sequence[EventHandler] = (),
        seen: SeenEvents | None = None,
    ) -> None:
        self.node_id = node_id
        self.router = router
        self.state = state
        self.state_path = Path(state_path)
        self.make_peer_link = make_peer_link
        self.broadcast = broadcast
        self.handlers: list[EventHandler] = list(handlers)
        self.seen = seen or SeenEvents()

    async def on_event(self, env: Envelope, *, local: bool = False) -> None:
        if not local and env.src is not None and env.src.node == self.node_id:
            return
        if self.seen.seen(env.id):
            return
        if env.hops >= MAX_EVENT_HOPS:
            log.warning(
                "Рой: событие %s (%s) пережило %d ретрансляций — дропаю (циркуляция?)",
                env.payload.get("event"),
                env.id,
                env.hops,
            )
            return
        if env.type == MSG_EVENT and env.payload.get("event") == EVENT_NODE_JOINED:
            data = env.payload.get("data", {})
            new_id = data.get("node_id")
            new_endpoint = data.get("endpoint")
            if (
                new_id
                and new_endpoint
                and new_id != self.node_id
                and new_id not in self.router.peers
            ):
                candidates = merge_endpoints(new_endpoint, data.get("endpoints"))
                link = self.make_peer_link(new_id, candidates)
                await self.router.add_peer(link)
                self.state.remember_peer(new_id, new_endpoint, candidates)
                self.state.save(self.state_path)
                log.info("Рой: авто-подключение к %s (%s) по node_joined", new_id, candidates)
        if self.broadcast is not None:
            await self.broadcast(dataclasses.replace(env, hops=env.hops + 1))
        if env.type == MSG_EVENT and env.payload.get("event") == EVENT_NODE_LEAVING:
            data = env.payload.get("data", {})
            leaving = data.get("node") or (env.src.node if env.src else None)
            left_link = self.router.peers.get(leaving) if leaving else None
            if left_link is not None:
                left_link.note_left()
        for handler in self.handlers:
            try:
                await handler(env)
            except Exception:
                log.exception("Ошибка в обработчике события")


from sa_home_node.swarm.events import (
    EVENT_NODE_JOINED,
    EVENT_NODE_LEAVING,
    EVENT_RESTART_APPLIED,
)

ANNOUNCE_VERSION_RETRY_S = 5.0
ANNOUNCE_VERSION_WINDOW_S = 120.0


async def announce_version(
    state: NodeState,
    state_path: str | Path,
    broadcast: Callable[[str, dict], Awaitable[int]],
    *,
    version: str = __version__,
    retry_s: float = ANNOUNCE_VERSION_RETRY_S,
    window_s: float = ANNOUNCE_VERSION_WINDOW_S,
) -> None:
    if state.last_known_version is None:
        state.last_known_version = version
        state.save(state_path)
        return
    if state.last_known_version == version:
        return
    data = {"from": state.last_known_version, "to": version}
    state.last_known_version = version
    state.save(state_path)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + window_s
    while True:
        if await broadcast(EVENT_RESTART_APPLIED, data) > 0:
            return
        if loop.time() >= deadline:
            log.warning(
                "restart_applied (%s -> %s): не удалось разослать за %.0fс — "
                "нет ни одного подключённого получателя",
                data["from"],
                data["to"],
                window_s,
            )
            return
        await asyncio.sleep(retry_s)
