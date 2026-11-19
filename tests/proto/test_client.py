from __future__ import annotations

import asyncio
import os
import socket

import pytest
from support import StubService, wait_for

from sa_home_node import host
from sa_home_node.proto.client import DEFAULT_TIMEOUT, ProtoClient
from sa_home_node.proto.describe import ActionSpec
from sa_home_node.proto.endpoints import TcpEndpoint
from sa_home_node.proto.server import ProtoServer
from sa_home_node.proto.wire import ProtoError

TOKEN = "test-token"
KINDS = ["unix", "tcp"]


class SlowService(StubService):
    def __init__(self) -> None:
        super().__init__(actions=(ActionSpec(id="slow", title="Медленно"),))

    async def run_command(self, action: str, args: dict) -> dict:
        await asyncio.sleep(0.3)
        return {"accepted": True}


class SpyHost:
    unix_sockets = True

    def __init__(self) -> None:
        self.keepalive_calls = 0
        self._real = host.current()

    def enable_tcp_keepalive(self, sock: socket.socket) -> None:
        self.keepalive_calls += 1
        self._real.enable_tcp_keepalive(sock)


class Kit:
    def __init__(self) -> None:
        self.servers: list[ProtoServer] = []

    async def start(self, endpoints: list, handler=None, **kw) -> ProtoServer:
        srv = ProtoServer(endpoints, handler or StubService(), **kw)
        self.servers.append(srv)
        await srv.start()
        return srv

    async def stop(self) -> None:
        for srv in self.servers:
            await srv.stop()
        self.servers.clear()


@pytest.fixture
def spy(monkeypatch):
    s = SpyHost()
    monkeypatch.setattr(host, "current", lambda: s)
    return s


@pytest.fixture
async def kit(spy, sock_dir):
    k = Kit()
    yield k
    await k.stop()


@pytest.fixture
async def clients(kit):
    out: list[ProtoClient] = []
    yield out
    for c in out:
        await c.close()


@pytest.fixture
async def server(kit, sock_dir):
    unix_path = os.path.join(sock_dir, "client.sock")
    return await kit.start([unix_path, TcpEndpoint("127.0.0.1", 0)], token=TOKEN)


async def connect_client(
    srv: ProtoServer,
    kind: str,
    clients: list[ProtoClient],
    *,
    on_event=None,
    timeout: float = DEFAULT_TIMEOUT,
    token: str = TOKEN,
) -> ProtoClient:
    if kind == "unix":
        ep = srv.endpoints[0].path
    else:
        ep = f"tcp://127.0.0.1:{srv.endpoints[1].port}"
    c = ProtoClient(ep, token=token, on_event=on_event, timeout=timeout)
    await c.connect()
    clients.append(c)
    return c


@pytest.mark.parametrize("kind", KINDS)
async def test_requests(server, clients, kind) -> None:
    c = await connect_client(server, kind, clients)
    info = await c.hello()
    assert (info.node, info.service, info.version) == ("node-a", "svc", "0.1.0")
    descr = await c.describe()
    assert descr.info.node == "node-a"
    assert descr.actions[0].id == "noop"
    assert await c.get_state() == {"node": "node-a"}
    assert await c.command("noop") == {"accepted": True}


async def test_tcp_wrong_token(server, clients) -> None:
    c = ProtoClient(f"tcp://127.0.0.1:{server.endpoints[1].port}", token="wrong")
    clients.append(c)
    with pytest.raises(ProtoError) as exc:
        await c.connect()
    assert exc.value.code == "unauthorized"
    assert not c.connected


async def test_timeout_override(kit, clients, sock_dir) -> None:
    unix_path = os.path.join(sock_dir, "slow.sock")
    srv = await kit.start([unix_path, TcpEndpoint("127.0.0.1", 0)], SlowService(), token=TOKEN)
    c = await connect_client(srv, "unix", clients, timeout=0.05)
    with pytest.raises(TimeoutError):
        await c.command("slow")
    assert await c.command("slow", timeout=2.0) == {"accepted": True}


async def test_request_after_server_stop(server, clients) -> None:
    c = await connect_client(server, "unix", clients)
    assert await c.get_state() == {"node": "node-a"}
    await server.stop()
    await wait_for(lambda: c._reader_task.done(), timeout=2.0)
    with pytest.raises((ConnectionError, OSError, ProtoError)):
        await c.get_state()


async def test_event_handler_queries_same_link(server, clients) -> None:
    done = asyncio.Event()
    answered: list[dict] = []
    holder: dict[str, ProtoClient] = {}

    async def on_event(env) -> None:
        answered.append(await holder["client"].get_state())
        done.set()

    c = await connect_client(server, "unix", clients, on_event=on_event)
    holder["client"] = c
    await c.hello()
    assert await server.broadcast_event("ev-1", {}) == 1
    await asyncio.wait_for(done.wait(), timeout=2.0)
    assert answered == [{"node": "node-a"}]


async def test_slow_handler_doesnt_block_responses(server, clients) -> None:
    started = asyncio.Event()

    async def on_event(env) -> None:
        started.set()
        await asyncio.sleep(0.5)

    c = await connect_client(server, "unix", clients, on_event=on_event)
    await c.hello()
    assert await server.broadcast_event("ev-1", {}) == 1
    await asyncio.wait_for(started.wait(), timeout=2.0)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    state = await c.get_state()
    assert loop.time() - t0 < 0.4
    assert state == {"node": "node-a"}


async def test_events_in_order(server, clients) -> None:
    seen: list[str] = []
    done = asyncio.Event()

    async def on_event(env) -> None:
        if len(seen) == 0:
            await asyncio.sleep(0.15)
        seen.append(env.payload["event"])
        if len(seen) == 2:
            done.set()

    c = await connect_client(server, "unix", clients, on_event=on_event)
    await c.hello()
    assert await server.broadcast_event("ev-1", {}) == 1
    await server.broadcast_event("ev-2", {})
    await asyncio.wait_for(done.wait(), timeout=2.0)
    assert seen == ["ev-1", "ev-2"]


async def test_join_delivers_event_before_eof(server, clients) -> None:
    seen: list[str] = []

    async def on_event(env) -> None:
        await asyncio.sleep(0.05)
        seen.append(env.payload["event"])

    c = await connect_client(server, "unix", clients, on_event=on_event)
    await c.hello()
    assert await server.broadcast_event("node_up", {}) == 1
    await server.stop()
    await asyncio.wait_for(c.join(), timeout=2.0)
    assert seen == ["node_up"]


@pytest.mark.parametrize("kind", KINDS)
async def test_keepalive(server, clients, spy, kind) -> None:
    c = await connect_client(server, kind, clients)
    if kind == "tcp":
        sock = c._writer.get_extra_info("socket")
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0
        assert spy.keepalive_calls >= 1
    else:
        assert spy.keepalive_calls == 0


@pytest.mark.parametrize("method", ["close", "abort"])
async def test_close_abort_from_handler(server, clients, method) -> None:
    after = asyncio.Event()
    holder: dict[str, ProtoClient] = {}

    async def on_event(env) -> None:
        c = holder["client"]
        if method == "close":
            await c.close()
        else:
            c.abort()
        after.set()

    c = await connect_client(server, "unix", clients, on_event=on_event)
    holder["client"] = c
    await c.hello()
    assert await server.broadcast_event("ev-1", {}) == 1
    await asyncio.wait_for(after.wait(), timeout=2.0)
    assert after.is_set()
