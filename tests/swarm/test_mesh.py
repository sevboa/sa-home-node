from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from netlab import NetLabProxy
from support import free_port, make_cfg, wait_for

from sa_home_node.config import NodeConfig
from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.wire import MSG_GET_STATE, Address, Envelope, make_request
from sa_home_node.swarm import link as link_mod
from sa_home_node.swarm.node import Swarm
from sa_home_node.swarm.state import NodeState

NODES = ("node-a", "node-b", "node-c")

Mesh = dict[str, tuple[Swarm, NodeConfig]]


@pytest.fixture(autouse=True)
def fast_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(link_mod, "HEARTBEAT_INTERVAL_S", 0.05)


async def start_mesh(
    sock_dir: str,
    tmp_path: Path,
    per_node_swarm: dict[str, dict] | None = None,
    ports: dict[str, int] | None = None,
) -> Mesh:
    used = ports or {node: free_port() for node in NODES}
    started: Mesh = {}
    try:
        for node in NODES:
            if per_node_swarm is not None:
                swarm_opts = per_node_swarm[node]
            elif node != "node-a":
                swarm_opts = {"join": f"tcp://127.0.0.1:{used['node-a']}"}
            else:
                swarm_opts = {}
            cfg = make_cfg(node, sock_dir, tmp_path, used[node], **swarm_opts)
            swarm = Swarm(cfg, NodeState.load(cfg.node.state_path))
            started[node] = (swarm, cfg)
            await swarm.start()
    except BaseException:
        await stop_mesh(started)
        raise
    return started


async def stop_mesh(started: Mesh) -> None:
    for swarm, _cfg in started.values():
        await swarm.stop()


async def wait_full_mesh(started: Mesh) -> None:
    for node in NODES:
        swarm = started[node][0]
        for other in NODES:
            if other != node:
                assert await wait_for(
                    lambda s=swarm, o=other: s.router.peers[o].alive, timeout=15.0
                ), f"{node} не видит {other}"


async def test_full_mesh_from_one_seed(sock_dir: str, tmp_path: Path) -> None:
    started = await start_mesh(sock_dir, tmp_path)
    try:
        await wait_full_mesh(started)
    finally:
        await stop_mesh(started)


async def test_events_delivered_once(sock_dir: str, tmp_path: Path) -> None:
    seen: dict[str, list[str]] = {node: [] for node in NODES}

    def make_cb(node: str) -> Callable[[Envelope], Awaitable[None]]:
        async def cb(env: Envelope) -> None:
            if env.payload.get("event") == "mesh_ping":
                seen[node].append(env.payload["data"]["from"])

        return cb

    started = await start_mesh(sock_dir, tmp_path)
    clients: list[ProtoClient] = []
    try:
        await wait_full_mesh(started)
        for node in NODES:
            client = ProtoClient(started[node][1].node.socket, on_event=make_cb(node))
            clients.append(client)
            await client.connect()
            await client.hello()
        for node in NODES:
            await started[node][0].emit("mesh_ping", {"from": node})
        assert await wait_for(lambda: all(len(seen[n]) == 3 for n in NODES), timeout=10.0)
        await asyncio.sleep(0.5)
        for node in NODES:
            assert sorted(seen[node]) == list(NODES)
    finally:
        for client in clients:
            await client.close()
        await stop_mesh(started)


async def test_request_bypasses_frozen_direct_path(sock_dir: str, tmp_path: Path) -> None:
    ports = {node: free_port() for node in NODES}

    def direct(node: str) -> dict:
        return {"id": node, "endpoint": f"tcp://127.0.0.1:{ports[node]}"}

    proxy = NetLabProxy("127.0.0.1", ports["node-c"])
    await proxy.start()
    try:
        per_node_swarm = {
            "node-a": {"nodes": [direct("node-b"), {"id": "node-c", "endpoint": proxy.endpoint}]},
            "node-b": {"nodes": [direct("node-a"), direct("node-c")]},
            "node-c": {"nodes": [direct("node-b")]},
        }
        started = await start_mesh(sock_dir, tmp_path, per_node_swarm, ports)
        a = started["node-a"][0]
        b = started["node-b"][0]
        try:
            for swarm, other in ((a, "node-b"), (a, "node-c"), (b, "node-a"), (b, "node-c")):
                assert await wait_for(
                    lambda s=swarm, o=other: s.router.peers[o].alive, timeout=15.0
                )
            proxy.freeze()
            assert await wait_for(lambda: not a.router.peers["node-c"].alive, timeout=10.0)
            resp = await a.request(
                make_request(MSG_GET_STATE, dst=Address(node="node-c", service="node"))
            )
            assert resp.payload["node"] == "node-c"
            peers_state = {p["id"]: p for p in a.router.peers_state()}
            assert resp.hops >= 1 or peers_state["node-c"].get("via") == "node-b"
        finally:
            await stop_mesh(started)
    finally:
        proxy.unfreeze()
        await proxy.stop()


async def test_restart_keeps_learned_peers_and_assignments(sock_dir: str, tmp_path: Path) -> None:
    NodeState(assignments=["svc-x", "svc-y"]).save(tmp_path / "node-b.json")
    started = await start_mesh(sock_dir, tmp_path)
    try:
        await wait_full_mesh(started)
        b, cfg_b = started["node-b"]
        await b.stop()
        state = NodeState.load(cfg_b.node.state_path)
        assert {"node-a", "node-c"} <= {p.id for p in state.peers}
        assert state.assignments == ["svc-x", "svc-y"]
        restarted = Swarm(cfg_b, state)
        await restarted.start()
        try:
            for other in ("node-a", "node-c"):
                assert await wait_for(
                    lambda o=other: restarted.router.peers[o].alive, timeout=15.0
                )
        finally:
            await restarted.stop()
    finally:
        await stop_mesh(started)
