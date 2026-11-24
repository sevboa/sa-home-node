from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sa_home_node.metrics.peerhost import peer_host
from sa_home_node.swarm.config import SwarmPeer


@dataclass(frozen=True)
class ScrapeTarget:
    node: str
    host: str


def scrape_targets(
    own_id: str,
    own_host: str | None,
    peers: Sequence[SwarmPeer],
) -> list[ScrapeTarget]:
    by_node: dict[str, str] = {}
    if own_id and own_host:
        by_node[own_id] = own_host
    for peer in peers:
        if peer.id == own_id or peer.id in by_node:
            continue
        host = peer_host([peer.endpoint, *peer.endpoints])
        if host:
            by_node[peer.id] = host
    return [ScrapeTarget(node, by_node[node]) for node in sorted(by_node)]
