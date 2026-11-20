from __future__ import annotations

import asyncio

import pytest
from support import free_port, make_cfg, wait_for

from sa_home_node.proto.describe import ActionSpec
from sa_home_node.proto.wire import (
    ERR_UNKNOWN_ACTION,
    MSG_GET_STATE,
    Address,
    Envelope,
    ProtoError,
    make_request,
)
from sa_home_node.swarm.config import SwarmPeer
from sa_home_node.swarm.node import Swarm, SwarmApi, peer_candidates, peer_kinds
from sa_home_node.swarm.state import NodeState


async def test_peer_candidates_merges_state_first_and_dedups() -> None:
    cfg_nodes = [
        SwarmPeer(
            id="node-a", endpoint="tcp://127.0.0.1:1", endpoints=["tcp://127.0.0.1:2"], kind="server"
        ),
        SwarmPeer(id="node-b", endpoint="", endpoints=[]),
    ]
    state_peers = [
        SwarmPeer(id="node-a", endpoint="tcp://127.0.0.1:9", endpoints=[]),
        SwarmPeer(id="node-c", endpoint="tcp://127.0.0.1:3", endpoints=[]),
    ]
    assert peer_candidates(cfg_nodes, state_peers) == {
        "node-a": ["tcp://127.0.0.1:9", "tcp://127.0.0.1:1", "tcp://127.0.0.1:2"],
        "node-b": [],
        "node-c": ["tcp://127.0.0.1:3"],
    }
    assert peer_kinds(cfg_nodes, state_peers) == {"node-a": "server"}


async def test_peer_kinds_first_kind_wins() -> None:
    nodes = [SwarmPeer(id="node-x", endpoint="tcp://127.0.0.1:1", kind="vps")]
    extra = [SwarmPeer(id="node-x", endpoint="tcp://127.0.0.1:2", kind="server")]
    assert peer_kinds(nodes, extra) == {"node-x": "server"}


async def test_swarm_builds_peer_links_and_skips_self(sock_dir: str, tmp_path) -> None:
    cfg = make_cfg(
        "node-a",
        sock_dir,
        tmp_path,
        free_port(),
        nodes=[
            {"id": "node-b", "endpoint": "tcp://127.0.0.1:1", "kind": "server"},
            {"id": "node-x", "endpoint": ""},
        ],
    )
    state = NodeState(peers=[SwarmPeer(id="node-c", endpoint="tcp://127.0.0.1:2")])
    swarm = Swarm(cfg, state)
    try:
        assert set(swarm.router.peers) == {"node-b", "node-c"}
        link_b = swarm.router.peers["node-b"]
        assert link_b.endpoints == ["tcp://127.0.0.1:1"]
        assert link_b.node_kind == "server"
        assert swarm.router.peers["node-c"].endpoints == ["tcp://127.0.0.1:2"]
        assert swarm.router.peers["node-c"].node_kind == ""
        assert "node-a" not in swarm.router.peers
    finally:
        await swarm.stop()


async def test_stop_without_start_and_twice(sock_dir: str, tmp_path) -> None:
    cfg = make_cfg("node-a", sock_dir, tmp_path, free_port())
    swarm = Swarm(cfg, NodeState())
    names: list[str] = []

    async def handler(env: Envelope) -> None:
        names.append(env.payload.get("event"))

    swarm.subscribe(handler)
    await swarm.stop()
    assert not names
    await swarm.start()
    await swarm.stop()
    await swarm.stop()
    assert names.count("node_leaving") == 1


async def test_two_nodes_find_each_other_and_greet(sock_dir: str, tmp_path) -> None:
    port_a = free_port()
    port_b = free_port()
    cfg_a = make_cfg(
        "node-a",
        sock_dir,
        tmp_path,
        port_a,
        nodes=[{"id": "node-b", "endpoint": f"tcp://127.0.0.1:{port_b}"}],
    )
    cfg_b = make_cfg("node-b", sock_dir, tmp_path, port_b, join=f"tcp://127.0.0.1:{port_a}")
    seen: list[Envelope] = []

    async def handler(env: Envelope) -> None:
        seen.append(env)

    swarm_a = Swarm(cfg_a, NodeState())
    swarm_a.subscribe(handler)
    swarm_b = Swarm(cfg_b, NodeState())
    restarted: Swarm | None = None
    try:
        await swarm_a.start()
        await swarm_b.start()
        assert await wait_for(lambda: swarm_a.router.peers["node-b"].alive, timeout=10.0)
        assert await wait_for(lambda: swarm_b.router.peers["node-a"].alive, timeout=10.0)
        peers_a = {p.id for p in NodeState.load(cfg_a.node.state_path).peers}
        peers_b = {p.id for p in NodeState.load(cfg_b.node.state_path).peers}
        assert "node-b" in peers_a
        assert "node-a" in peers_b

        resp_b = await swarm_a.request(
            make_request(MSG_GET_STATE, dst=Address(node="node-b", service="node"))
        )
        assert resp_b.payload["node"] == "node-b"
        resp_a = await swarm_a.request(
            make_request(MSG_GET_STATE, dst=Address(node="node-a", service="node"))
        )
        assert resp_a.payload["node"] == "node-a"

        await swarm_b.emit("example_greeting", {"x": 1})
        assert await wait_for(
            lambda: any(e.payload.get("event") == "example_greeting" for e in seen)
        )
        greeting = next(e for e in seen if e.payload.get("event") == "example_greeting")
        assert greeting.src is not None
        assert greeting.src.node == "node-b"

        seen.clear()
        await swarm_b.stop()
        assert await wait_for(lambda: swarm_a.router.peers["node-b"].left, timeout=5.0)
        assert any(e.payload.get("event") == "node_leaving" for e in seen)

        seen.clear()
        restarted = Swarm(cfg_b, NodeState.load(cfg_b.node.state_path))
        await restarted.start()
        assert await wait_for(lambda: swarm_a.router.peers["node-b"].alive, timeout=10.0)
        await asyncio.sleep(1.0)
        assert not any(e.payload.get("event") == "restart_applied" for e in seen)

        await restarted.stop()
        seen.clear()
        version_b = Swarm(cfg_b, NodeState.load(cfg_b.node.state_path), version="9.9.9")
        restarted = version_b
        await restarted.start()
        assert await wait_for(
            lambda: any(e.payload.get("event") == "restart_applied" for e in seen), timeout=15.0
        )
        applied = next(e for e in seen if e.payload.get("event") == "restart_applied")
        assert applied.payload["data"]["to"] == "9.9.9"
    finally:
        if restarted is not None:
            await restarted.stop()
        await swarm_a.stop()


async def test_swarm_api_commands_between_nodes(sock_dir: str, tmp_path) -> None:
    port_a = free_port()
    port_b = free_port()
    cfg_a = make_cfg(
        "node-a",
        sock_dir,
        tmp_path,
        port_a,
        nodes=[{"id": "node-b", "endpoint": f"tcp://127.0.0.1:{port_b}"}],
    )
    cfg_b = make_cfg("node-b", sock_dir, tmp_path, port_b, join=f"tcp://127.0.0.1:{port_a}")
    async def ping(args: dict) -> dict:
        return {"pong": args.get("text", "")}

    swarm_a = Swarm(cfg_a, NodeState())
    swarm_b = Swarm(cfg_b, NodeState())
    swarm_b.node_service.actions.add(ActionSpec(id="p1_ping", title="Пинг p1"), ping)
    api = SwarmApi(swarm_a)
    try:
        await swarm_a.start()
        await swarm_b.start()
        assert await wait_for(lambda: swarm_a.router.peers["node-b"].alive, timeout=10.0)
        assert api.node_id == "node-a"
        result = await api.command("node-b", "p1_ping", {"text": "hi"})
        assert result == {"pong": "hi"}
        assert (await api.get_state("node-b"))["node"] == "node-b"
        desc = await api.describe("node-b")
        assert any(a["id"] == "p1_ping" for a in desc["actions"])
        with pytest.raises(ProtoError) as exc:
            await api.command("node-b", "нет_такого", {})
        assert exc.value.code == ERR_UNKNOWN_ACTION
    finally:
        await swarm_b.stop()
        await swarm_a.stop()
