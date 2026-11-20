from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sa_home_node.proto.server import ProtoServer
from sa_home_node.proto.wire import ERR_BAD_REQUEST, ERR_UNAVAILABLE, ProtoError
from sa_home_node.swarm.events import ACTION_SWARM_JOIN, EVENT_NODE_JOINED
from sa_home_node.swarm.link import PeerLink
from sa_home_node.swarm.router import NodeRouter
from sa_home_node.swarm.service import ACTION_JOIN, NodeService
from sa_home_node.swarm.state import NodeState

FIXTURES = Path(__file__).parent.parent / "fixtures" / "wire"
OWN = ("tcp://127.0.0.1:8710", "tcp://127.0.0.1:8711")
Pair = tuple[NodeService, NodeService, NodeRouter, NodeRouter, list[str]]


def _load_json(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())


async def test_describe_without_own_endpoint() -> None:
    svc = NodeService("node-a")
    action_ids = [a.id for a in svc.describe().actions]
    assert ACTION_SWARM_JOIN not in action_ids
    assert ACTION_JOIN not in action_ids


async def test_describe_with_own_endpoint_matches_fixture() -> None:
    svc = NodeService("node-a", own_endpoint="tcp://127.0.0.1:8710")
    action_map = {a.id: a for a in svc.describe().actions}
    fixture = _load_json("node-actions.json")
    for spec_id in (ACTION_SWARM_JOIN, ACTION_JOIN):
        assert action_map[spec_id].to_dict() == next(i for i in fixture if i["id"] == spec_id)


SWARM_JOIN_BAD_ARGS = (
    {"node_id": "node-b"},
    {"endpoint": "tcp://127.0.0.1:8711"},
    {"node_id": "node-a", "endpoint": "tcp://127.0.0.1:8710"},
)


@pytest.mark.parametrize("args", SWARM_JOIN_BAD_ARGS)
async def test_swarm_join_bad_request(args: dict) -> None:
    svc = NodeService("node-a", own_endpoint="tcp://127.0.0.1:8710")
    with pytest.raises(ProtoError) as exc:
        await svc.run_command("swarm_join", args)
    assert exc.value.code == ERR_BAD_REQUEST


async def test_join_empty_endpoint() -> None:
    svc = NodeService("node-a", own_endpoint="tcp://127.0.0.1:8710")
    with pytest.raises(ProtoError) as exc:
        await svc.run_command("join", {"endpoint": ""})
    assert exc.value.code == ERR_BAD_REQUEST


@pytest.fixture
def recording_router(monkeypatch: pytest.MonkeyPatch) -> tuple[NodeRouter, list[str], list[str]]:
    router = NodeRouter("node-a")
    created: list[str] = []
    removed: list[str] = []

    async def add_peer(link: PeerLink) -> None:
        created.append(link.name)
        router.peers[link.name] = link

    async def remove_peer(node_id: str) -> None:
        removed.append(node_id)
        router.peers.pop(node_id, None)

    monkeypatch.setattr(router, "add_peer", add_peer)
    monkeypatch.setattr(router, "remove_peer", remove_peer)
    return router, created, removed


SWARM_JOIN_LINK_CASES = (
    (
        {"node_id": "node-b", "endpoint": "tcp://127.0.0.1:8720", "endpoints": ["tcp://127.0.0.1:8721"]},
        ["tcp://127.0.0.1:8720", "tcp://127.0.0.1:8721"],
    ),
    ({"node_id": "node-b", "endpoint": "tcp://127.0.0.1:8720"}, ["tcp://127.0.0.1:8720"]),
)


@pytest.mark.parametrize(("args", "expected"), SWARM_JOIN_LINK_CASES)
async def test_swarm_join_link_endpoints(
    recording_router: tuple[NodeRouter, list[str], list[str]],
    args: dict,
    expected: list[str],
) -> None:
    router, created, _removed = recording_router
    svc = NodeService("node-a", own_endpoint=OWN[0], router=router, advertise=lambda: list(OWN))

    result = await svc.run_command("swarm_join", args)

    assert created == ["node-b"]
    assert router.peers["node-b"].endpoints == expected
    assert result["peers"][0]["endpoints"] == list(OWN)


async def test_swarm_join_reply_format_matches_fixture(
    recording_router: tuple[NodeRouter, list[str], list[str]],
) -> None:
    router, _created, _removed = recording_router
    svc = NodeService("node-a", own_endpoint=OWN[0], router=router, advertise=lambda: list(OWN))

    result = await svc.run_command(
        "swarm_join",
        {"node_id": "node-b", "endpoint": "tcp://127.0.0.1:8720", "endpoints": ["tcp://127.0.0.1:8721"]},
    )

    fixture = _load_json("swarm-join-reply.json")["peers"]
    assert list(result["peers"][0].keys()) == list(fixture[0].keys())
    assert list(result["peers"][1].keys()) == list(fixture[1].keys())


async def test_swarm_join_emits_node_joined() -> None:
    emitted: list[tuple[str, dict]] = []

    async def emit(event: str, data: dict) -> None:
        emitted.append((event, data))

    svc = NodeService("node-a", own_endpoint="tcp://127.0.0.1:8710", emit=emit)
    await svc.run_command(
        "swarm_join",
        {"node_id": "node-b", "endpoint": "tcp://127.0.0.1:8720", "endpoints": ["tcp://127.0.0.1:8721"]},
    )

    assert emitted == [
        (
            EVENT_NODE_JOINED,
            {
                "node_id": "node-b",
                "endpoint": "tcp://127.0.0.1:8720",
                "endpoints": ["tcp://127.0.0.1:8720", "tcp://127.0.0.1:8721"],
            },
        )
    ]


@pytest.fixture
async def pair(sock_dir: str, tmp_path: Path):
    created: list[str] = []

    def make_link(name: str, eps: list[str]) -> PeerLink:
        created.append(name)
        return PeerLink(name, eps, token="test-token")

    router_a = NodeRouter("node-a")
    router_b = NodeRouter("node-b")
    svc_a = NodeService(
        "node-a",
        router=router_a,
        own_endpoint=os.path.join(sock_dir, "node-a.sock"),
        state_path=tmp_path / "node-a.json",
        make_peer_link=make_link,
        swarm_token="test-token",
    )
    svc_b = NodeService(
        "node-b",
        router=router_b,
        own_endpoint=os.path.join(sock_dir, "node-b.sock"),
        state_path=tmp_path / "node-b.json",
        make_peer_link=make_link,
        swarm_token="test-token",
    )
    server_a = ProtoServer(svc_a.advertised()[0], svc_a, router=router_a.route)
    server_b = ProtoServer(svc_b.advertised()[0], svc_b, router=router_b.route)
    await server_a.start()
    await server_b.start()
    try:
        yield svc_a, svc_b, router_a, router_b, created
    finally:
        for link in list(router_a.peers.values()) + list(router_b.peers.values()):
            await link.stop()
        await server_a.stop()
        await server_b.stop()


async def test_join_two_nodes(pair: Pair, tmp_path: Path) -> None:
    svc_a, svc_b, router_a, router_b, _created = pair

    result = await svc_b.join(svc_a.advertised()[0])

    assert result["peers_added"] == ["node-a"]
    assert "node-b" in router_a.peers
    assert "node-a" in router_b.peers
    state_a = NodeState.load(tmp_path / "node-a.json")
    state_b = NodeState.load(tmp_path / "node-b.json")
    assert any(p.id == "node-b" for p in state_a.peers)
    assert any(p.id == "node-a" for p in state_b.peers)


async def test_join_idempotent(pair: Pair) -> None:
    svc_a, svc_b, router_a, _router_b, _created = pair

    await svc_b.join(svc_a.advertised()[0])
    first_link = router_a.peers["node-b"]

    await svc_b.join(svc_a.advertised()[0])

    assert router_a.peers["node-b"] is first_link


async def test_join_custom_factory(pair: Pair) -> None:
    svc_a, svc_b, _router_a, _router_b, created = pair

    await svc_b.join(svc_a.advertised()[0])

    assert "node-a" in created
    assert "node-b" in created


async def test_join_unavailable(sock_dir: str) -> None:
    svc = NodeService("node-a", own_endpoint=OWN[0], swarm_token="test-token")
    with pytest.raises(ProtoError) as exc:
        await svc.join(os.path.join(sock_dir, "ghost.sock"))
    assert exc.value.code == ERR_UNAVAILABLE
