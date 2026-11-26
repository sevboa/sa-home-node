from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def format_peer_path(peer: Mapping[str, Any]) -> str:
    parts: list[str] = []
    rtt = peer.get("rtt_ms")
    if rtt is not None:
        text = f"{round(rtt)} мс"
        loss = peer.get("loss") or 0
        if loss > 0:
            text += f", потери {round(loss * 100)}%"
        parts.append(text)
    via = peer.get("via")
    if via:
        text = f"через {via}"
        via_rtt = peer.get("via_rtt_ms")
        if via_rtt is not None:
            text += f" ~{round(via_rtt)} мс"
        parts.append(text)
    return " · ".join(parts)
