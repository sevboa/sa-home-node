from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from pathlib import Path

import pytest
from support import StubService

from sa_home_node import host
from sa_home_node.proto.chunks import download_chunks
from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.describe import ActionParam, ActionSpec, ServiceDescription, ServiceInfo
from sa_home_node.proto.endpoints import TcpEndpoint
from sa_home_node.proto.server import ProtoServer
from sa_home_node.proto.wire import (
    ERR_BAD_REQUEST,
    ERR_INTERNAL,
    ERR_UNAUTHORIZED,
    ERR_UNKNOWN_ACTION,
    MSG_DESCRIBE,
    MSG_GET_STATE,
    MSG_HELLO,
    Address,
    ProtoError,
    decode,
    encode,
    make_request,
)

TOKEN = "test-token"
KINDS = ["unix", "tcp"]


class SpyHost:
    unix_sockets = True

    def __init__(self) -> None:
        self.socks: list[object] = []
        self._real = host.current()

    def enable_tcp_keepalive(self, sock: object) -> None:
        self.socks.append(sock)
        self._real.enable_tcp_keepalive(sock)


class CyrillicService(StubService):
    def describe(self) -> ServiceDescription:
        return ServiceDescription(
            info=ServiceInfo(
                node="node-a",
                service="svc",
                version="0.1.0",
                node_kind="домашняя нода",
                wake={"будильник": "в 07:00"},
            ),
            actions=(
                ActionSpec(
                    id="ping",
                    title="Пинг-сервис",
                    params=(ActionParam(name="цель", title="Кому пингнуть"),),
                ),
            ),
        )


class ErrorService(StubService):
    def __init__(self) -> None:
        super().__init__(
            actions=(
                ActionSpec(id="noop", title="Ничего"),
                ActionSpec(
                    id="guarded", title="Охраняемое", params=(ActionParam(name="target"),)
                ),
                ActionSpec(id="boom", title="Падающее"),
            )
        )

    async def run_command(self, action: str, args: dict) -> dict:
        if action == "boom":
            raise RuntimeError("упасть")
        return {"accepted": True}


class ChunkService(StubService):
    def __init__(self, payload: bytes) -> None:
        super().__init__(actions=(ActionSpec(id="chunk", title="Файл"),))
        self.payload = payload

    async def run_command(self, action: str, args: dict) -> dict:
        data = self.payload[args["offset"] : args["offset"] + args["length"]]
        return {
            "data_b64": base64.b64encode(data).decode(),
            "eof": args["offset"] + len(data) >= len(self.payload),
        }


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


async def test_wire_raw_bytes_no_spaces_no_unicode_escape(kit, clients, sock_dir) -> None:
    unix_path = os.path.join(sock_dir, "e2e-wire.sock")
    await kit.start([unix_path], CyrillicService())
    reader, writer = await asyncio.open_unix_connection(unix_path)
    for type_ in (MSG_HELLO, MSG_DESCRIBE):
        writer.write(encode(make_request(type_)))
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
        assert b'", "' not in raw
        assert b'": "' not in raw
        assert b"\\u" not in raw
        assert "домашняя нода".encode() in raw
        assert "будильник".encode() in raw
        assert decode(raw).ok
        if type_ == MSG_DESCRIBE:
            assert "Пинг-сервис".encode() in raw
            assert "Кому пингнуть".encode() in raw
    writer.close()
    await writer.wait_closed()


async def test_dual_listeners_unix_and_tcp(kit, clients, sock_dir) -> None:
    unix_path = os.path.join(sock_dir, "e2e-dual.sock")
    server = await kit.start([unix_path, TcpEndpoint("127.0.0.1", 0)], StubService(), token=TOKEN)
    unix_ep, tcp_ep = server.endpoints
    got_unix = asyncio.Event()
    got_tcp = asyncio.Event()

    async def on_unix_event(env) -> None:
        got_unix.set()

    async def on_tcp_event(env) -> None:
        got_tcp.set()

    local = ProtoClient(unix_ep, on_event=on_unix_event, timeout=5.0)
    await local.connect()
    clients.append(local)
    assert (await local.hello()).node == "node-a"
    peer = ProtoClient(tcp_ep, token=TOKEN, on_event=on_tcp_event, timeout=5.0)
    await peer.connect()
    clients.append(peer)
    assert (await peer.hello()).node == "node-a"
    assert await server.broadcast_event("x", {}) == 2
    await asyncio.wait_for(got_unix.wait(), timeout=5.0)
    await asyncio.wait_for(got_tcp.wait(), timeout=5.0)
    reader, writer = await asyncio.open_connection(tcp_ep.host, tcp_ep.port)
    writer.write(encode(make_request(MSG_GET_STATE)))
    await writer.drain()
    raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
    assert decode(raw).error_code() == ERR_UNAUTHORIZED
    writer.close()
    await writer.wait_closed()


@pytest.mark.parametrize("kind", KINDS)
async def test_keepalive_both_sides(kit, clients, spy, sock_dir, kind) -> None:
    if kind == "tcp":
        srv = await kit.start([TcpEndpoint("127.0.0.1", 0)], token=TOKEN)
        ep = f"tcp://127.0.0.1:{srv.endpoints[0].port}"
    else:
        unix_path = os.path.join(sock_dir, "e2e-ka.sock")
        srv = await kit.start([unix_path])
        ep = unix_path
    c = ProtoClient(ep, token=TOKEN, timeout=5.0)
    await c.connect()
    clients.append(c)
    assert (await c.hello()).node == "node-a"
    if kind == "tcp":
        assert len(spy.socks) == 2
        assert len([s for s in spy.socks if s.getsockname()[1] == srv.endpoints[0].port]) == 1
    else:
        assert spy.socks == []


def test_sys_platform_only_in_host() -> None:
    src_root = Path(__file__).parents[2] / "src"
    allowed = src_root / "sa_home_node" / "host" / "__init__.py"
    hits = [p for p in sorted(src_root.rglob("*.py")) if "sys.platform" in p.read_text()]
    assert hits == [allowed]


@pytest.mark.parametrize(
    ("action", "args", "code"),
    [
        ("nope", {}, ERR_UNKNOWN_ACTION),
        ("guarded", {}, ERR_BAD_REQUEST),
        ("boom", {"target": "x"}, ERR_INTERNAL),
    ],
)
async def test_client_error_codes(kit, clients, sock_dir, action, args, code) -> None:
    unix_path = os.path.join(sock_dir, "e2e-err.sock")
    await kit.start([unix_path], ErrorService())
    c = ProtoClient(unix_path, timeout=5.0)
    await c.connect()
    clients.append(c)
    with pytest.raises(ProtoError) as err:
        await asyncio.wait_for(c.command(action, args), timeout=5.0)
    assert err.value.code == code


async def test_download_chunks_over_wire(kit, clients, sock_dir) -> None:
    unix_path = os.path.join(sock_dir, "e2e-chunks.sock")
    payload = bytes(range(256)) * (1_500_000 // 256 + 1)
    await kit.start([unix_path], ChunkService(payload))
    c = ProtoClient(unix_path, timeout=5.0)
    await c.connect()
    clients.append(c)
    got = await asyncio.wait_for(
        download_chunks(
            c,
            "chunk",
            Address(node="node-a", service="svc"),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        ),
        timeout=15.0,
    )
    assert got == payload
