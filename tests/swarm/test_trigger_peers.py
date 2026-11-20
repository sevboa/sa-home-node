from __future__ import annotations

import json
from pathlib import Path

import pytest

from sa_home_node.proto.wire import (
    ERR_BAD_REQUEST,
    ERR_UNKNOWN_DST,
    Envelope,
    ProtoError,
    make_error_response,
    make_response,
)
from sa_home_node.swarm.link import PeerLink
from sa_home_node.swarm.router import NodeRouter
from sa_home_node.swarm.service import ACTION_TRIGGER_PEERS, NodeService

FIXTURES = Path(__file__).parent.parent / "fixtures" / "wire"

BAD_ARGS = (
    {"action": "check"},
    {"service": "probe"},
    {"service": "probe", "action": "check", "args": ["x"]},
)


class ProbeService:
    def __init__(self, name: str) -> None:
        self.name = name
        self.alive = True
        self.busy = False
        self.working = True
        self.seen: list[Envelope] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def forward(self, env: Envelope) -> Envelope:
        self.seen.append(env)
        return make_response(env, {"probed": self.name})


class PeerClient:
    def __init__(self, name: str, *, mode: str = "ok") -> None:
        self.name = name
        self.mode = mode
        self.seen: list[Envelope] = []

    async def forward(self, env: Envelope) -> Envelope:
        self.seen.append(env)
        if self.mode == "unknown_dst":
            return make_error_response(env.id, ERR_UNKNOWN_DST, f"нет службы у {self.name}")
        if self.mode == "raise":
            raise ConnectionError("обрыв связи")
        return make_response(env, {"answered_by": self.name})

    def abort(self) -> None:
        return None


def make_link(name: str, *, client: PeerClient | None = None) -> PeerLink:
    link = PeerLink(name, "tcp://127.0.0.1:1")
    link._client = client
    return link


async def make_svc(*peers: PeerLink, probe: bool = True) -> tuple[NodeService, ProbeService]:
    router = NodeRouter("node-a", peers={link.name: link for link in peers})
    local = ProbeService("probe")
    if probe:
        await router.add_local_service("probe", local)
    return NodeService("node-a", router=router), local


async def test_local_node_with_service() -> None:
    svc, local = await make_svc()
    result = await svc.trigger_peers(
        {"service": "probe", "action": "check", "args": {"targets": ["x"]}}
    )
    assert result == {"dispatched": ["node-a"], "skipped": [], "unreachable": []}
    assert local.seen[0].payload == {"action": "check", "args": {"targets": ["x"]}}


async def test_local_node_without_service() -> None:
    svc, _local = await make_svc(probe=False)
    result = await svc.trigger_peers({"service": "probe", "action": "check"})
    assert result == {"dispatched": [], "skipped": ["node-a"], "unreachable": []}


async def test_alive_peer_receives_payload() -> None:
    client = PeerClient("node-b")
    svc, _local = await make_svc(make_link("node-b", client=client))
    result = await svc.trigger_peers(
        {"service": "probe", "action": "check", "args": {"targets": ["x"]}}
    )
    assert result == {"dispatched": ["node-a", "node-b"], "skipped": [], "unreachable": []}
    assert client.seen[0].payload == {"action": "check", "args": {"targets": ["x"]}}


async def test_peer_without_service_or_broken() -> None:
    unknown = PeerClient("node-b", mode="unknown_dst")
    broken = PeerClient("node-c", mode="raise")
    svc, _local = await make_svc(
        make_link("node-b", client=unknown), make_link("node-c", client=broken), probe=False
    )
    result = await svc.trigger_peers({"service": "probe", "action": "check"})
    assert result == {"dispatched": [], "skipped": ["node-a", "node-b"], "unreachable": ["node-c"]}


async def test_dead_peer_is_not_contacted() -> None:
    dead = make_link("node-c")
    alive = make_link("node-b", client=PeerClient("node-b"))
    svc, _local = await make_svc(alive, dead)
    result = await svc.trigger_peers({"service": "probe", "action": "check"})
    assert result == {"dispatched": ["node-a", "node-b"], "skipped": [], "unreachable": []}


async def test_without_router_own_node_unreachable() -> None:
    svc = NodeService("node-a")
    result = await svc.trigger_peers({"service": "probe", "action": "check"})
    assert result == {"dispatched": [], "skipped": [], "unreachable": ["node-a"]}


@pytest.mark.parametrize("args", BAD_ARGS)
async def test_bad_request(args: dict) -> None:
    svc = NodeService("node-a")
    with pytest.raises(ProtoError) as exc:
        await svc.trigger_peers(args)
    assert exc.value.code == ERR_BAD_REQUEST


async def test_describe_matches_fixture() -> None:
    svc = NodeService("node-a")
    spec = next(a for a in svc.describe().actions if a.id == ACTION_TRIGGER_PEERS)
    fixture = json.loads((FIXTURES / "node-actions.json").read_text())
    assert spec.to_dict() == next(i for i in fixture if i["id"] == ACTION_TRIGGER_PEERS)
