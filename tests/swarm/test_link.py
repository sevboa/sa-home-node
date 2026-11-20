from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from netlab import NetLabProxy
from support import TOKEN, StubService, start_server, wait_for

from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.endpoints import TcpEndpoint
from sa_home_node.proto.server import ProtoServer
from sa_home_node.proto.wire import ERR_TIMEOUT, ERR_UNAVAILABLE, MSG_COMMAND, ProtoError, make_request
from sa_home_node.swarm import link as link_mod
from sa_home_node.swarm.link import PeerLink


@contextlib.asynccontextmanager
async def serving(
    sock_dir: str, servers: list[tuple[str, StubService | None]], **link_kw: object
) -> AsyncIterator[tuple[PeerLink, dict[str, str]]]:
    paths: dict[str, str] = {}
    started: list[ProtoServer] = []
    for name, service in servers:
        path = os.path.join(sock_dir, f"{name}.sock")
        paths[name] = path
        if service is not None:
            started.append(await start_server(f"unix:{path}", service))
    kw = {"reconnect_delay": 0.05, **link_kw}
    link = PeerLink("node-a", [f"unix:{p}" for p in paths.values()], token=TOKEN, **kw)
    try:
        await link.start()
        yield link, paths
    finally:
        await link.stop()
        for server in started:
            await server.stop()


@pytest.mark.parametrize(
    ("servers", "link_kw", "alive", "endpoint_name", "kind"),
    [
        pytest.param(
            [("dead", None), ("live", StubService(node="node-a"))],
            {},
            True,
            "live",
            "",
            id="first-working",
        ),
        pytest.param(
            [("wrong", StubService(node="node-b")), ("right", StubService(node="node-a"))],
            {},
            True,
            "right",
            "",
            id="correct-over-wrong",
        ),
        pytest.param(
            [("wrong", StubService(node="node-b"))],
            {},
            True,
            "wrong",
            "",
            id="single-wrong",
        ),
        pytest.param(
            [("self", StubService(node="node-b")), ("other", StubService(node="node-a"))],
            {"self_node": "node-b"},
            True,
            "other",
            "",
            id="self-skipped",
        ),
        pytest.param(
            [("self", StubService(node="node-b"))],
            {"self_node": "node-b"},
            False,
            "self",
            "",
            id="only-self",
        ),
        pytest.param(
            [
                ("wrong", StubService(node="node-b", node_kind="workstation")),
                ("right", StubService(node="node-a")),
            ],
            {},
            True,
            "right",
            "",
            id="foreign-kind",
        ),
        pytest.param(
            [("link", StubService(node="node-a", node_kind="server"))],
            {},
            True,
            "link",
            "server",
            id="confirmed-kind",
        ),
    ],
)
async def test_link_address_choice(
    sock_dir: str,
    servers: list[tuple[str, StubService | None]],
    link_kw: dict,
    alive: bool,
    endpoint_name: str,
    kind: str,
) -> None:
    async with serving(sock_dir, servers, **link_kw) as (link, paths):
        if not alive:
            await asyncio.sleep(0.2)
            assert not link.alive
            return
        assert await wait_for(lambda: link.alive)
        assert str(link.endpoint) == paths[endpoint_name]
        assert link.endpoints[0] == paths[endpoint_name]
        assert link.node_kind == kind


@pytest.mark.parametrize(
    ("servers", "learned"),
    [
        pytest.param(
            [("link", StubService(node="node-a", endpoints=("tcp://192.0.2.5:8710", "tcp://100.64.0.5:8710")))],
            [("link", None), (None, "tcp://192.0.2.5:8710"), (None, "tcp://100.64.0.5:8710")],
            id="learns",
        ),
        pytest.param(
            [("link", StubService(node="node-a", endpoints=("tcp://127.0.0.1:8710",)))],
            [("link", None)],
            id="loopback-dropped",
        ),
        pytest.param(
            [
                ("stale", None),
                ("right", StubService(node="node-a", endpoints=("tcp://192.0.2.3:8710",))),
            ],
            [("right", None), (None, "tcp://192.0.2.3:8710")],
            id="stale-dropped",
        ),
        pytest.param(
            [("link", StubService(node="node-b", endpoints=("tcp://192.0.2.4:8710",)))],
            [("link", None)],
            id="mismatched-ignored",
        ),
    ],
)
async def test_link_learned_endpoints(
    sock_dir: str,
    servers: list[tuple[str, StubService | None]],
    learned: list[tuple[str | None, str]],
) -> None:
    async with serving(sock_dir, servers) as (link, paths):
        assert await wait_for(lambda: link.alive)
        expected = [paths[name] if name is not None else addr for name, addr in learned]
        assert await wait_for(lambda: link.endpoints == expected)


async def test_link_reconnect_now_drops_and_reconnects(sock_dir: str) -> None:
    async with serving(sock_dir, [("link", StubService(node="node-a"))]) as (link, _paths):
        assert await wait_for(lambda: link.alive)

        link.reconnect_now("test reconnect")
        assert await wait_for(lambda: not link.alive)
        assert await wait_for(lambda: link.alive)


async def test_link_survives_exception_in_hello(sock_dir: str) -> None:
    async with serving(sock_dir, [("link", StubService(node="node-a"))]) as (link, _paths):
        original_hello = ProtoClient.hello

        async def failing_hello(self, dst=None):
            if not hasattr(failing_hello, "called"):
                failing_hello.called = True
                raise RuntimeError("injected failure")
            return await original_hello(self, dst)

        with patch.object(ProtoClient, "hello", failing_hello):
            assert await wait_for(lambda: link.alive)


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
    class SlowService(StubService):
        async def run_command(self, action: str, args: dict) -> dict:
            await asyncio.sleep(1.0)
            return await super().run_command(action, args)

    async with serving(sock_dir, [("link", SlowService(node="node-a"))]) as (link, _paths):
        assert await wait_for(lambda: link.alive)

        env = make_request(MSG_COMMAND, {"action": "noop", "args": {}}, timeout_s=0.1)
        with pytest.raises(ProtoError) as exc_info:
            await link.forward(env)
        assert exc_info.value.code == ERR_TIMEOUT
        assert link.alive


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


async def test_link_heartbeat_drops_on_silent_peer(sock_dir: str, monkeypatch) -> None:
    monkeypatch.setattr(link_mod, "HEARTBEAT_INTERVAL_S", 0.05)
    monkeypatch.setattr(link_mod, "HEARTBEAT_MISSES", 2)

    async with serving(
        sock_dir,
        [("link", StubService(node="node-a"))],
        reconnect_delay=60.0,
        heartbeat_timeout=0.05,
    ) as (link, _paths):
        assert await wait_for(lambda: link.alive)

        async def failing_hello(self, dst=None):
            raise TimeoutError("injected")

        with patch.object(ProtoClient, "hello", failing_hello):
            assert await wait_for(lambda: not link.alive, timeout=5.0)


async def test_link_heartbeat_freeze_drops_link(sock_dir: str, monkeypatch) -> None:
    monkeypatch.setattr(link_mod, "HEARTBEAT_INTERVAL_S", 0.05)

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
        reconnect_delay=60.0,
        heartbeat_timeout=0.05,
    )
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)

        proxy.freeze()
        assert await wait_for(lambda: not link.alive, timeout=5.0)
        assert link.loss > 0
    finally:
        await link.stop()
        await proxy.stop()
        await server.stop()


async def test_link_heartbeat_survives_reconnect_storm(sock_dir: str, monkeypatch) -> None:
    monkeypatch.setattr(link_mod, "HEARTBEAT_INTERVAL_S", 0.02)
    monkeypatch.setattr(link_mod, "HEARTBEAT_MISSES", 1)

    async with serving(
        sock_dir,
        [("link", StubService(node="node-a"))],
        heartbeat_timeout=0.02,
    ) as (link, _paths):
        assert await wait_for(lambda: link.alive)

        for _ in range(5):
            link.reconnect_now("test")
            link.reconnect_now("test again")
            await asyncio.sleep(0.03)

        assert await wait_for(lambda: link.alive, timeout=10.0)


async def test_link_heartbeat_picks_up_new_endpoint(sock_dir: str, monkeypatch) -> None:
    monkeypatch.setattr(link_mod, "HEARTBEAT_INTERVAL_S", 0.02)
    lan = "tcp://192.0.2.9:8710"
    overlay = "tcp://100.64.0.9:8710"

    service = StubService(node="node-a", endpoints=(lan,))
    calls: list[tuple[str, list[str]]] = []

    def on_endpoints(name: str, eps: list[str]) -> None:
        calls.append((name, eps))

    async with serving(sock_dir, [("link", service)], on_endpoints=on_endpoints) as (link, paths):
        assert await wait_for(lambda: link.alive)
        assert await wait_for(lambda: link.endpoints == [paths["link"], lan])

        service.endpoints = (lan, overlay)
        assert await wait_for(lambda: overlay in link.endpoints)
        assert await wait_for(lambda: calls[-1][1] == [paths["link"], lan, overlay])


async def test_link_busy_working_from_hello(sock_dir: str) -> None:
    sock = os.path.join(sock_dir, "link.sock")

    service = StubService(node="node-a", busy=True, working=True)
    server = await start_server(f"unix:{sock}", service)

    link = PeerLink(
        "node-a",
        f"unix:{sock}",
        token=TOKEN,
        reconnect_delay=0.05,
    )
    try:
        await link.start()
        assert await wait_for(lambda: link.alive)
        assert await wait_for(lambda: link.busy is True)
        assert await wait_for(lambda: link.working is True)

        await server.stop()
        assert await wait_for(lambda: not link.alive)
        assert link.busy is False
        assert link.working is None
    finally:
        await link.stop()
        await server.stop()


def test_link_quality_metrics_no_network() -> None:
    link = PeerLink("node-a", "unix:/tmp/fake.sock", token=TOKEN, reconnect_delay=60.0)

    link.note_probe(100.0)
    assert link.rtt_ms == 100.0
    assert link.loss == 0.0

    link.note_probe(200.0)
    assert abs(link.rtt_ms - 130.0) < 0.01

    link.note_probe(None)
    assert link.loss > 0

    for _ in range(12):
        link.note_probe(50.0)
    assert len(link._probes) == 12

    link.note_probe(60.0)
    assert len(link._probes) == 12

    assert link.cost_ms() is None
    link._client = object()
    cost = link.cost_ms()
    assert cost is not None
    assert cost >= link.rtt_ms
    link._client = None

    q = link.quality()
    assert q["alive"] is False
    assert q["rtt_ms"] is not None
    assert q["loss"] >= 0.0
