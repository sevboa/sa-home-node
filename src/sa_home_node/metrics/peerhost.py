from __future__ import annotations

from collections.abc import Iterable

from sa_home_node.proto.endpoints import (
    RANK_OVERLAY,
    TcpEndpoint,
    endpoint_rank,
    is_loopback,
    is_unspecified,
    parse_endpoint,
)


def peer_host(candidates: Iterable[str]) -> str | None:
    best: tuple[bool, int, int] | None = None
    winner: str | None = None
    for index, value in enumerate(candidates):
        try:
            endpoint = parse_endpoint(value)
        except ValueError:
            continue
        if not isinstance(endpoint, TcpEndpoint):
            continue
        if is_loopback(endpoint) or is_unspecified(endpoint):
            continue
        key = (endpoint_rank(endpoint) != RANK_OVERLAY, endpoint_rank(endpoint), index)
        if best is None or key < best:
            best = key
            winner = endpoint.host
    return winner
