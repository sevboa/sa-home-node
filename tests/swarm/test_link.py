from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

import pytest
from netlab import NetLabProxy
from support import StubService, wait_for

from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.endpoints import TcpEndpoint
from sa_home_node.proto.server import ProtoServer
from sa_home_node.proto.wire import ERR_TIMEOUT, ERR_UNAVAILABLE, MSG_COMMAND, ProtoError, make_request
from sa_home_node.swarm.link import PeerLink


TOKEN = "test-token"


async def _start_server(endpoint: str, service: StubService) -> ProtoServer:
    server = ProtoServer(endpoint, service, token=TOKEN)
    await server.start()
    return server


async def test_link_picks_first_working_address(sock_dir: str) -> None:
    dead_sock = os.path.join(sock_dir, "dead.sock")
    live_sock = os.path.join(sock_dir, "live.sock")

    service = StubService(node="node-a")
    server = await _start_server(f"unix:{live_sock}", service)

    link = PeerLink(
        "node-a",
        [f"unix:{dead_sock}", f"unix:{live_sock}"],
        token=TOKEN,
        reconnect_delay=0.05,
    )
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)
        assert str(link.endpoint) == live_sock
        assert link.endpoints[0] == live_sock
    finally:
        await link.stop()
        await server.stop()


async def test_link_prefers_correct_node_over_wrong_one(sock_dir: str) -> None:
    wrong_sock = os.path.join(sock_dir, "wrong.sock")
    right_sock = os.path.join(sock_dir, "right.sock")

    wrong_service = StubService(node="node-b")
    right_service = StubService(node="node-a")

    wrong_server = await _start_server(f"unix:{wrong_sock}", wrong_service)
    right_server = await _start_server(f"unix:{right_sock}", right_service)

    link = PeerLink(
        "node-a",
        [f"unix:{wrong_sock}", f"unix:{right_sock}"],
        token=TOKEN,
        reconnect_delay=0.05,
    )
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)
        assert str(link.endpoint) == right_sock
    finally:
        await link.stop()
        await wrong_server.stop()
        await right_server.stop()


async def test_link_single_wrong_node_still_works(sock_dir: str) -> None:
    wrong_sock = os.path.join(sock_dir, "wrong.sock")

    wrong_service = StubService(node="node-b")
    server = await _start_server(f"unix:{wrong_sock}", wrong_service)

    link = PeerLink(
        "node-a",
        f"unix:{wrong_sock}",
        token=TOKEN,
        reconnect_delay=0.05,
    )
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)
    finally:
        await link.stop()
        await server.stop()


async def test_link_self_node_address_skipped_when_other_exists(sock_dir: str) -> None:
    self_sock = os.path.join(sock_dir, "self.sock")
    other_sock = os.path.join(sock_dir, "other.sock")

    self_service = StubService(node="node-b")
    other_service = StubService(node="node-a")

    self_server = await _start_server(f"unix:{self_sock}", self_service)
    other_server = await _start_server(f"unix:{other_sock}", other_service)

    link = PeerLink(
        "node-a",
        [f"unix:{self_sock}", f"unix:{other_sock}"],
        token=TOKEN,
        reconnect_delay=0.05,
        self_node="node-b",
    )
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)
        assert str(link.endpoint) == other_sock
    finally:
        await link.stop()
        await self_server.stop()
        await other_server.stop()


async def test_link_only_self_node_address_not_alive(sock_dir: str) -> None:
    self_sock = os.path.join(sock_dir, "self.sock")

    self_service = StubService(node="node-b")
    server = await _start_server(f"unix:{self_sock}", self_service)

    link = PeerLink(
        "node-a",
        f"unix:{self_sock}",
        token=TOKEN,
        reconnect_delay=0.05,
        self_node="node-b",
    )
    try:
        await link.start()
        await asyncio.sleep(0.2)
        assert not link.alive
    finally:
        await link.stop()
        await server.stop()


async def test_link_foreign_node_kind_not_learned(sock_dir: str) -> None:
    wrong_sock = os.path.join(sock_dir, "wrong.sock")
    right_sock = os.path.join(sock_dir, "right.sock")

    wrong_service = StubService(node="node-b", node_kind="workstation")
    right_service = StubService(node="node-a")

    wrong_server = await _start_server(f"unix:{wrong_sock}", wrong_service)
    right_server = await _start_server(f"unix:{right_sock}", right_service)

    link = PeerLink(
        "node-a",
        [f"unix:{wrong_sock}", f"unix:{right_sock}"],
        token=TOKEN,
        reconnect_delay=0.05,
    )
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)
        assert link.node_kind == ""
    finally:
        await link.stop()
        await wrong_server.stop()
        await right_server.stop()


async def test_link_confirmed_node_kind_learned(sock_dir: str) -> None:
    sock = os.path.join(sock_dir, "link.sock")

    service = StubService(node="node-a", node_kind="server")
    server = await _start_server(f"unix:{sock}", service)

    link = PeerLink(
        "node-a",
        f"unix:{sock}",
        token=TOKEN,
        reconnect_delay=0.05,
    )
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)
        assert link.node_kind == "server"
    finally:
        await link.stop()
        await server.stop()


async def test_link_reconnect_now_drops_and_reconnects(sock_dir: str) -> None:
    sock = os.path.join(sock_dir, "link.sock")

    service = StubService(node="node-a")
    server = await _start_server(f"unix:{sock}", service)

    link = PeerLink(
        "node-a",
        f"unix:{sock}",
        token=TOKEN,
        reconnect_delay=0.05,
    )
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)

        link.reconnect_now("test reconnect")
        assert await wait_for(lambda: not link.alive)
        assert await wait_for(lambda: link.alive)
    finally:
        await link.stop()
        await server.stop()


async def test_link_survives_exception_in_hello(sock_dir: str) -> None:
    sock = os.path.join(sock_dir, "link.sock")

    service = StubService(node="node-a")
    server = await _start_server(f"unix:{sock}", service)

    link = PeerLink(
        "node-a",
        f"unix:{sock}",
        token=TOKEN,
        reconnect_delay=0.05,
    )
    try:
        await link.start()

        original_hello = ProtoClient.hello

        async def failing_hello(self, dst=None):
            if not hasattr(failing_hello, "called"):
                failing_hello.called = True
                raise RuntimeError("injected failure")
            return await original_hello(self, dst)

        with patch.object(ProtoClient, "hello", failing_hello):
            assert await wait_for(lambda: link.alive)
    finally:
        await link.stop()
        await server.stop()


async def test_link_forward_without_connection_raises_unavailable(sock_dir: str) -> None:
    link = PeerLink("node-a", f"unix:{sock_dir}/nonexistent.sock", token=TOKEN, reconnect_delay=0.05)
    env = make_request(MSG_COMMAND, {"action": "noop", "args": {}})
    try:
        with pytest.raises(ProtoError) as exc_info:
            await link.forward(env)
        assert exc_info.value.code == ERR_UNAVAILABLE
    finally:
        await link.stop()


async def test_link_forward_timeout_keeps_link_alive(sock_dir: str) -> None:
    sock = os.path.join(sock_dir, "link.sock")

    class SlowService(StubService):
        async def run_command(self, action: str, args: dict) -> dict:
            await asyncio.sleep(1.0)
            return await super().run_command(action, args)

    service = SlowService(node="node-a")
    server = await _start_server(f"unix:{sock}", service)

    link = PeerLink(
        "node-a",
        f"unix:{sock}",
        token=TOKEN,
        reconnect_delay=0.05,
    )
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)

        env = make_request(MSG_COMMAND, {"action": "noop", "args": {}}, timeout_s=0.1)
        with pytest.raises(ProtoError) as exc_info:
            await link.forward(env)
        assert exc_info.value.code == ERR_TIMEOUT
        assert link.alive
    finally:
        await link.stop()
        await server.stop()


async def test_link_recovers_after_tcp_drop() -> None:
    service = StubService(node="node-a")
    server = ProtoServer(TcpEndpoint("127.0.0.1", 0), service, token=TOKEN)
    await server.start()
    server_port = server.endpoint.port

    proxy = NetLabProxy("127.0.0.1", server_port)
    await proxy.start()

    link = PeerLink(
        "node-a",
        proxy.endpoint,
        token=TOKEN,
        reconnect_delay=0.05,
    )
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)

        await proxy.stop()
        assert await wait_for(lambda: not link.alive)

        await proxy.start()
        assert await wait_for(lambda: link.alive)
    finally:
        await link.stop()
        await proxy.stop()
        await server.stop()
