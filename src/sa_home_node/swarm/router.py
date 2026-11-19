from __future__ import annotations

import dataclasses
import logging
from typing import Protocol

from sa_home_node.proto.wire import (
    ERR_UNAVAILABLE,
    ERR_UNKNOWN_DST,
    MSG_DESCRIBE,
    MSG_GET_STATE,
    MSG_HELLO,
    NODE_SERVICE,
    Envelope,
    ProtoError,
)
from sa_home_node.swarm.link import LOSS_PENALTY_MS, PeerLink

log = logging.getLogger(__name__)

SWITCH_RATIO = 0.8
SWITCH_MIN_GAIN_MS = 30.0
RETRYABLE_TYPES = frozenset({MSG_HELLO, MSG_DESCRIBE, MSG_GET_STATE})


class LocalTarget(Protocol):
    name: str
    alive: bool
    busy: bool
    working: bool | None

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def forward(self, env: Envelope) -> Envelope: ...


class NodeRouter:
    def __init__(
        self,
        node_id: str,
        *,
        peers: dict[str, PeerLink] | None = None,
        local_services: dict[str, LocalTarget] | None = None,
    ) -> None:
        self.node_id = node_id
        self.peers = peers or {}
        self.local_services = local_services or {}
        self._via: dict[str, str] = {}

    def busy_services(self) -> list[str]:
        return sorted(name for name, link in self.local_services.items() if link.alive and link.busy)

    def working_services(self) -> list[str]:
        return sorted(
            name
            for name, link in self.local_services.items()
            if link.alive and (link.busy if link.working is None else link.working)
        )

    async def route(self, request: Envelope) -> Envelope | None:
        dst = request.dst
        if dst is None:
            return None
        if dst.node is not None and dst.node != self.node_id:
            peer = self.peers.get(dst.node)
            if peer is None:
                known = ", ".join(self.peers) or "нет пиров"
                raise ProtoError(ERR_UNKNOWN_DST, f"неизвестная нода: {dst.node} (есть: {known})")
            return await self._forward_to_node(peer, request)
        if dst.service is not None and dst.service != NODE_SERVICE:
            link = self.local_services.get(dst.service)
            if link is None:
                known = ", ".join(self.local_services) or "нет служб"
                raise ProtoError(ERR_UNKNOWN_DST, f"нет такой службы: {dst.service} (есть: {known})")
            return await link.forward(request)
        return None

    async def _forward_to_node(self, peer: PeerLink, request: Envelope) -> Envelope:
        if request.hops > 0:
            return await peer.forward(request)
        relay = self._choose_relay(peer)
        if relay is not None:
            return await relay.forward(dataclasses.replace(request, hops=request.hops + 1))
        try:
            return await peer.forward(request)
        except ProtoError as exc:
            if exc.code != ERR_UNAVAILABLE or request.type not in RETRYABLE_TYPES:
                raise
            fallback = self._best_relay(peer)
            if fallback is None:
                raise
            relay_link, _ = fallback
            return await relay_link.forward(dataclasses.replace(request, hops=request.hops + 1))

    def _relay_cost(self, relay: PeerLink, target: str) -> float | None:
        first = relay.cost_ms()
        entry = relay.peer_links.get(target)
        if first is None or not entry or entry.get("alive") is not True:
            return None
        rtt = entry.get("rtt_ms")
        if rtt is None:
            return None
        return first + rtt + float(entry.get("loss") or 0.0) * LOSS_PENALTY_MS

    def _best_relay(self, target: PeerLink) -> tuple[PeerLink, float] | None:
        best = None
        for link in self.peers.values():
            if link is target:
                continue
            cost = self._relay_cost(link, target.name)
            if cost is not None and (best is None or cost < best[1]):
                best = (link, cost)
        return best

    def _choose_relay(self, target: PeerLink) -> PeerLink | None:
        best = self._best_relay(target)
        direct = target.cost_ms()
        current = self.peers.get(self._via.get(target.name, ""))
        current_cost = self._relay_cost(current, target.name) if current is not None else None
        if current_cost is None:
            current, current_cost = None, direct
        if not target.alive:
            chosen = best[0] if best is not None else None
        elif current_cost is None:
            chosen = current
        else:
            candidates = [(None, direct)] if current is not None else []
            if best is not None and best[0] is not current:
                candidates.append(best)
            chosen = current
            for link, cost in candidates:
                if cost is None:
                    continue
                if cost < current_cost * SWITCH_RATIO and current_cost - cost > SWITCH_MIN_GAIN_MS:
                    chosen, current_cost = link, cost
        self._remember_path(target.name, chosen)
        return chosen

    def _remember_path(self, target: str, relay: PeerLink | None) -> None:
        previous = self._via.get(target)
        if relay is None:
            self._via.pop(target, None)
        else:
            self._via[target] = relay.name
        if previous != self._via.get(target):
            log.info("Маршрут к %s: %s", target, f"через {relay.name}" if relay is not None else "напрямую")

    def links_table(self) -> dict[str, dict[str, object]]:
        return {link.name: link.quality() for link in self.peers.values()}

    def peers_state(self) -> list[dict[str, object]]:
        out = []
        for link in self.peers.values():
            via = self._via.get(link.name)
            relay = self.peers.get(via) if via else None
            via_cost = self._relay_cost(relay, link.name) if relay is not None else None
            out.append({
                "id": link.name,
                "endpoint": str(link.endpoint),
                "endpoints": link.endpoints,
                "alive": link.alive,
                "kind": link.node_kind,
                "down_s": link.downtime_s(),
                "left": link.left,
                "wake": link.wake_info,
                "rtt_ms": round(link.rtt_ms, 1) if link.rtt_ms is not None else None,
                "loss": round(link.loss, 3),
                "via": relay.name if relay is not None else None,
                "via_rtt_ms": round(via_cost, 1) if via_cost is not None else None,
            })
        return out

    async def add_local_service(self, name: str, link: LocalTarget) -> None:
        self.local_services[name] = link
        await link.start()

    async def remove_local_service(self, name: str) -> None:
        link = self.local_services.pop(name, None)
        if link is not None:
            await link.stop()

    async def add_peer(self, link: PeerLink) -> None:
        self.peers[link.name] = link
        await link.start()

    async def remove_peer(self, node_id: str) -> None:
        link = self.peers.pop(node_id, None)
        if link is not None:
            await link.stop()
