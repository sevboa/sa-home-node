from __future__ import annotations

import asyncio
import contextlib
import os

import pytest
from netlab import NetLabProxy
from support import StubService, wait_for

from sa_home_node.proto.endpoints import TcpEndpoint
from sa_home_node.proto.server import ProtoServer
from sa_home_node.proto.wire import (
    ERR_TIMEOUT,
    ERR_UNAVAILABLE,
    ERR_UNKNOWN_DST,
    MSG_GET_STATE,
    Address,
    ProtoError,
    make_request,
)
from sa_home_node.swarm import link as link_mod
from sa_home_node.swarm.link import PeerLink
from sa_home_node.swarm.router import NodeRouter

TOKEN = "test-token"


async def _start_server(endpoint: str, service: StubService) -> ProtoServer:
    server = ProtoServer(endpoint, service, token=TOKEN)
    await server.start()
    return server


async def test_freeze_drops_link_then_unfreeze_recovers(monkeypatch) -> None:
    monkeypatch.setattr(link_mod, "HEARTBEAT_INTERVAL_S", 0.1)
    monkeypatch.setattr(link_mod, "HEARTBEAT_MISSES", 2)

    service = StubService(node="node-b")
    server = ProtoServer(TcpEndpoint("127.0.0.1", 0), service, token=TOKEN)
    await server.start()
    server_port = server.endpoint.port

    proxy = NetLabProxy("127.0.0.1", server_port)
    await proxy.start()

    link = PeerLink(
        "node-b",
        proxy.endpoint,
        token=TOKEN,
        reconnect_delay=0.1,
        heartbeat_timeout=0.1,
    )
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)

        proxy.freeze()
        assert await wait_for(lambda: not link.alive, timeout=1.0)

        proxy.unfreeze()
        assert await wait_for(lambda: link.alive, timeout=2.0)
    finally:
        await link.stop()
        await proxy.stop()
        await server.stop()


async def test_direct_path_degradation_fallback_and_recovery(monkeypatch) -> None:
    monkeypatch.setattr(link_mod, "HEARTBEAT_INTERVAL_S", 0.05)

    service_b = StubService(
        node="node-b",
        state={"node": "node-b", "cpu_c": 10.0},
    )
    server_b = ProtoServer(TcpEndpoint("127.0.0.1", 0), service_b, token=TOKEN)
    await server_b.start()
    port_b = server_b.endpoint.port

    proxy = NetLabProxy("127.0.0.1", port_b)
    await proxy.start()

    link_b = PeerLink(
        "node-b",
        proxy.endpoint,
        token=TOKEN,
        reconnect_delay=60.0,
        heartbeat_timeout=2.0,
    )

    service_c = StubService(
        node="node-c",
        links={"node-b": {"rtt_ms": 80.0, "loss": 0.0, "alive": True}},
        state={"node": "node-c", "cpu_c": 20.0},
    )
    server_c = ProtoServer(TcpEndpoint("127.0.0.1", 0), service_c, token=TOKEN)
    await server_c.start()
    port_c = server_c.endpoint.port

    link_c = PeerLink(
        "node-c",
        f"tcp://127.0.0.1:{port_c}",
        token=TOKEN,
        reconnect_delay=60.0,
        heartbeat_timeout=2.0,
    )

    router = NodeRouter("node-a", peers={"node-b": link_b, "node-c": link_c})
    await link_b.start()
    await link_c.start()

    try:
        assert await wait_for(lambda: link_b.alive)
        assert await wait_for(lambda: link_c.alive)

        req = make_request(MSG_GET_STATE, dst=Address(node="node-b", service="node"))

        resp = await router.route(req)
        assert resp is not None
        assert resp.payload == {"node": "node-b", "cpu_c": 10.0}
        assert resp.hops == 0

        proxy.delay_s = 0.3
        assert await wait_for(lambda: link_b.rtt_ms is not None and link_b.rtt_ms > 200, timeout=5.0)

        resp = await router.route(req)
        assert resp is not None
        assert resp.payload == {"node": "node-c", "cpu_c": 20.0}
        state = router.peers_state()
        b_state = next(s for s in state if s["id"] == "node-b")
        assert b_state["via"] == "node-c"

        proxy.delay_s = 0.0
        assert await wait_for(lambda: link_b.rtt_ms is not None and link_b.rtt_ms < 50, timeout=5.0)

        resp = await router.route(req)
        assert resp is not None
        assert resp.payload == {"node": "node-b", "cpu_c": 10.0}
        state = router.peers_state()
        b_state = next(s for s in state if s["id"] == "node-b")
        assert b_state["via"] is None
    finally:
        await link_b.stop()
        await link_c.stop()
        await proxy.stop()
        await server_b.stop()
        await server_c.stop()


async def test_stale_address_moved_to_end(monkeypatch) -> None:
    monkeypatch.setattr(link_mod, "HEARTBEAT_INTERVAL_S", 0.05)
    monkeypatch.setattr(link_mod, "HEARTBEAT_MISSES", 1)

    service1 = StubService(node="node-b")
    server1 = ProtoServer(TcpEndpoint("127.0.0.1", 0), service1, token=TOKEN)
    await server1.start()
    port1 = server1.endpoint.port

    service2 = StubService(node="node-b")
    server2 = ProtoServer(TcpEndpoint("127.0.0.1", 0), service2, token=TOKEN)
    await server2.start()
    port2 = server2.endpoint.port

    link = PeerLink(
        "node-b",
        [f"tcp://127.0.0.1:{port1}", f"tcp://127.0.0.1:{port2}"],
        token=TOKEN,
        reconnect_delay=0.05,
    )
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)
        assert link.endpoint.port == port1

        await server1.stop()
        await wait_for(lambda: not link.alive, timeout=2.0)

        assert await wait_for(lambda: link.alive, timeout=2.0)
        assert link.endpoint.port == port2

        await link.stop()
    finally:
        await server1.stop()
        await server2.stop()


async def test_forward_without_connection_raises_unavailable() -> None:
    link = PeerLink("node-b", "tcp://127.0.0.1:1", token=TOKEN, reconnect_delay=0.05)
    env = make_request(MSG_GET_STATE, dst=Address(node="node-b", service="node"))
    try:
        with pytest.raises(ProtoError) as exc_info:
            await link.forward(env)
        assert exc_info.value.code == ERR_UNAVAILABLE
    finally:
        await link.stop()


async def test_forward_timeout_keeps_link_alive(sock_dir: str) -> None:
    class SlowService(StubService):
        async def get_state(self) -> dict:
            await asyncio.sleep(1.0)
            return await super().get_state()

    sock = os.path.join(sock_dir, "slow.sock")
    with contextlib.suppress(OSError):
        os.unlink(sock)

    service = SlowService(node="node-b")
    server = await _start_server(f"unix:{sock}", service)

    link = PeerLink(
        "node-b",
        f"unix:{sock}",
        token=TOKEN,
        reconnect_delay=0.05,
    )
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)

        env = make_request(MSG_GET_STATE, dst=Address(node="node-b", service="node"), timeout_s=0.1)
        with pytest.raises(ProtoError) as exc_info:
            await link.forward(env)
        assert exc_info.value.code == ERR_TIMEOUT
        assert link.alive
    finally:
        await link.stop()
        await server.stop()
        with contextlib.suppress(OSError):
            os.unlink(sock)


async def test_kind_wake_addresses_only_on_confirmed_response(monkeypatch) -> None:
    monkeypatch.setattr(link_mod, "HEARTBEAT_INTERVAL_S", 0.05)

    service = StubService(node="node-b", node_kind="server")
    server = ProtoServer(TcpEndpoint("127.0.0.1", 0), service, token=TOKEN)
    await server.start()
    port = server.endpoint.port

    link = PeerLink("node-b", f"tcp://127.0.0.1:{port}", token=TOKEN, reconnect_delay=0.05)
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)
        assert link.node_kind == "server"
        assert len(link.endpoints) >= 1
    finally:
        await link.stop()
        await server.stop()


async def test_loss_window_12(monkeypatch) -> None:
    monkeypatch.setattr(link_mod, "HEARTBEAT_INTERVAL_S", 0.05)
    monkeypatch.setattr(link_mod, "HEARTBEAT_MISSES", 30)

    service = StubService(node="node-b")
    server = ProtoServer(TcpEndpoint("127.0.0.1", 0), service, token=TOKEN)
    await server.start()
    port = server.endpoint.port

    proxy = NetLabProxy("127.0.0.1", port)
    await proxy.start()

    link = PeerLink(
        "node-b",
        proxy.endpoint,
        token=TOKEN,
        reconnect_delay=60.0,
        heartbeat_timeout=0.1,
    )
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)

        await asyncio.sleep(0.5)

        proxy.freeze()
        await asyncio.sleep(2.0)
        assert link.loss > 0.9

        proxy.unfreeze()
        await asyncio.sleep(2.0)
        assert link.loss < 0.1
    finally:
        await link.stop()
        await proxy.stop()
        await server.stop()


async def test_unknown_dst_raises() -> None:
    router = NodeRouter("node-a", peers={})
    req = make_request(MSG_GET_STATE, dst=Address(node="node-z", service="node"))
    with pytest.raises(ProtoError) as exc_info:
        await router.route(req)
    assert exc_info.value.code == ERR_UNKNOWN_DST


async def test_dead_peer_returns_unavailable() -> None:
    service = StubService(node="node-b")
    server = ProtoServer(TcpEndpoint("127.0.0.1", 0), service, token=TOKEN)
    await server.start()
    port = server.endpoint.port

    link = PeerLink("node-b", f"tcp://127.0.0.1:{port}", token=TOKEN, reconnect_delay=60.0)
    router = NodeRouter("node-a", peers={"node-b": link})

    await link.start()
    try:
        assert await wait_for(lambda: link.alive)

        await server.stop()
        assert await wait_for(lambda: not link.alive)

        req = make_request(MSG_GET_STATE, dst=Address(node="node-b", service="node"))
        with pytest.raises(ProtoError) as exc_info:
            await router.route(req)
        assert exc_info.value.code == ERR_UNAVAILABLE
    finally:
        await link.stop()
