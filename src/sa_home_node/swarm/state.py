from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, Field

from sa_home_node.files import atomic_write_text
from sa_home_node.swarm.config import SwarmPeer


class NodeState(BaseModel):
    assignments: list[str] = Field(default_factory=list)
    peers: list[SwarmPeer] = Field(default_factory=list)
    last_known_version: str | None = None

    @classmethod
    def load(cls, path: str | Path) -> NodeState:
        path = Path(path)
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_bytes())

    def save(self, path: str | Path) -> None:
        atomic_write_text(path, self.model_dump_json(indent=2))

    def remember_peer(self, node_id: str, endpoint: str, endpoints: Sequence[str] = ()) -> None:
        known = self._peer(node_id)
        others = [p for p in self.peers if p.id != node_id]
        self.peers = [
            *others,
            SwarmPeer(
                id=node_id,
                endpoint=endpoint,
                endpoints=[e for e in endpoints if e != endpoint],
                kind=known.kind if known is not None else "",
            ),
        ]

    def record_peer_kind(
        self, node_id: str, endpoint: str, endpoints: Sequence[str], kind: str
    ) -> bool:
        known = self._peer(node_id)
        if known is None:
            self.peers.append(
                SwarmPeer(
                    id=node_id,
                    endpoint=endpoint,
                    endpoints=[e for e in endpoints if e != endpoint],
                    kind=kind,
                )
            )
            return True
        if known.kind != kind:
            known.kind = kind
            return True
        return False

    def peer_kind(self, node_id: str) -> str:
        known = self._peer(node_id)
        return known.kind if known is not None else ""

    def _peer(self, node_id: str) -> SwarmPeer | None:
        return next((p for p in self.peers if p.id == node_id), None)

