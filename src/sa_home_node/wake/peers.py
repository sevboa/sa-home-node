from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Protocol

from sa_home_node.proto.wire import NODE_SERVICE, Address
from sa_home_node.services.periodic import PEER_ERRORS

PEER_TIMEOUT_S = 3.0
MONITOR_SERVICE = "monitor"
WAKE_KEY_PREFIX = "wake_info:"


class NodeLink(Protocol):
    async def get_state(self, dst: Address | None = None) -> dict: ...

    async def command(
        self,
        action: str,
        args: dict[str, Any] | None = None,
        dst: Address | None = None,
        *,
        timeout: float | None = None,
    ) -> dict: ...


class WakeCache(Protocol):
    async def get(self, node: str) -> dict | None: ...

    async def put(self, node: str, wake: dict | None) -> None: ...


class MemoryWakeCache:
    def __init__(self) -> None:
        self._items: dict[str, str] = {}

    async def get(self, node: str) -> dict | None:
        raw = self._items.get(node)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) and data.get("mac") else None

    async def put(self, node: str, wake: dict | None) -> None:
        if not wake or not wake.get("mac"):
            return
        self._items[node] = json.dumps(wake)


@dataclass
class NodeReport:
    node_id: str
    alive: bool
    state: dict | None = None
    monitor: dict | None = None
    kind: str = ""
    left: bool = False


async def fetch_state(link: NodeLink, dst: Address | None, *, timeout_s: float | None = None) -> dict | None:
    limit = PEER_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        return await asyncio.wait_for(link.get_state(dst=dst), limit)
    except PEER_ERRORS:
        return None


async def collect_reports(link: NodeLink, own_state: dict, *, with_monitor: bool = False) -> list[NodeReport]:
    reports = [
        NodeReport(
            node_id=own_state.get("node", "?"),
            alive=True,
            state=own_state,
            kind=own_state.get("kind", ""),
        )
    ]
    for peer in own_state.get("peers", []):
        reports.append(
            NodeReport(
                node_id=peer.get("id", "?"),
                alive=bool(peer.get("alive")),
                kind=str(peer.get("kind") or ""),
                left=bool(peer.get("left")),
            )
        )

    async def fill(report: NodeReport) -> None:
        if report.state is None:
            report.state = await fetch_state(link, Address(node=report.node_id, service=NODE_SERVICE))
        if with_monitor:
            report.monitor = await fetch_state(link, Address(node=report.node_id, service=MONITOR_SERVICE))

    await asyncio.gather(*(fill(r) for r in reports if r.alive))
    return reports


async def resolve_wake_info(link: NodeLink, cache: WakeCache, node_id: str) -> dict | None:
    info = await cache.get(node_id)
    if info is not None:
        return info
    try:
        own_state = await link.get_state()
    except PEER_ERRORS:
        return None
    for peer in own_state.get("peers", []):
        if peer.get("id") != node_id:
            continue
        await cache.put(node_id, peer.get("wake"))
        return await cache.get(node_id)
    return None


async def find_lan_waker(
    link: NodeLink, cache: WakeCache, target_node_id: str, target_broadcast: str
) -> str | None:
    try:
        own_state = await link.get_state()
    except PEER_ERRORS:
        return None
    reports = await collect_reports(link, own_state)
    for r in reports:
        if r.state is not None:
            await cache.put(r.node_id, r.state.get("wake"))
    for r in reports:
        if not r.alive or r.node_id == target_node_id or r.state is None:
            continue
        candidate = r.state.get("wake")
        if candidate and candidate.get("broadcast") == target_broadcast:
            return r.node_id
    return None
