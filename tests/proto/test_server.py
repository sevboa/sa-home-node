from __future__ import annotations

import asyncio
import os

import pytest

from sa_home_node.proto.describe import ActionParam, ActionSpec, ServiceDescription, ServiceInfo
from sa_home_node.proto.endpoints import TcpEndpoint
from sa_home_node.proto.server import ProtoServer
from sa_home_node.proto.wire import (
    ERR_BAD_REQUEST,
    ERR_INTERNAL,
    ERR_UNKNOWN_ACTION,
    MSG_AUTH,
    MSG_COMMAND,
    MSG_DESCRIBE,
    MSG_EVENT,
    MSG_GET_STATE,
    MSG_HELLO,
    Envelope,
    decode,
    encode,
    make_request,
    make_response,
)

TOKEN = "test-token"


class Handler:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def describe(self) -> ServiceDescription:
        return ServiceDescription(
            info=ServiceInfo(node="node-a", service="svc", version="0.1.0"),
            actions=(
                ActionSpec(id="ping", title="Пинг", params=(ActionParam(name="target"),)),
            ),
        )

    async def get_state(self) -> dict:
        return {"n": 1}

    async def run_command(self, action: str, args: dict) -> dict:
        if self.fail:
            raise RuntimeError("упасть")
        return {"accepted": True}


class Conn:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer

    async def send(self, env: Envelope | bytes) -> None:
        self.writer.write(env if isinstance(env, bytes) else encode(env))
        await self.writer.drain()

    async def line(self, timeout: float = 5.0) -> bytes:
        return await asyncio.wait_for(self.reader.readline(), timeout)

    async def read(self, timeout: float = 5.0) -> Envelope:
        return decode(await self.line(timeout))


async def unix_conn(path: str) -> Conn:
    reader, writer = await asyncio.open_unix_connection(path)
    return Conn(reader, writer)


async def tcp_conn(port: int) -> Conn:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    return Conn(reader, writer)


class Kit:
    def __init__(self) -> None:
        self.servers: list[ProtoServer] = []

    async def start(self, endpoints: list, handler=None, **kw) -> ProtoServer:
        srv = ProtoServer(endpoints, handler or Handler(), **kw)
        await srv.start()
        self.servers.append(srv)
        return srv

    async def stop(self) -> None:
        for srv in self.servers:
            await srv.stop()
        self.servers.clear()


@pytest.fixture
def unix_ep(sock_dir) -> str:
    return os.path.join(sock_dir, "test.sock")


@pytest.fixture
async def kit():
    k = Kit()
    yield k
    await k.stop()


async def auth(conn: Conn, **kw) -> Envelope:
    payload = {"token": TOKEN, "node": "node-a", "incarnation": "inc-1"}
    payload.update(kw)
    await conn.send(make_request(MSG_AUTH, payload))
    return await conn.read()


def test_ctor_variants(unix_ep) -> None:
    for endpoints in (unix_ep, [unix_ep]):
        srv = ProtoServer(endpoints, Handler())
        assert len(srv.endpoints) == 1
        assert srv.bound_endpoints == ()
        assert srv.advertised_endpoints() == []
        assert srv.connection_count == 0


@pytest.mark.parametrize(
    ("endpoints", "token"),
    [([], ""), ([TcpEndpoint("127.0.0.1", 0)], ""), ([TcpEndpoint("127.0.0.1", 0)], None)],
)
def test_ctor_rejects(endpoints, token) -> None:
    with pytest.raises(ValueError):
        ProtoServer(endpoints, Handler(), token=token)


async def test_unix_requests(kit, unix_ep) -> None:
    await kit.start([unix_ep])
    conn = await unix_conn(unix_ep)
    await conn.send(make_request(MSG_HELLO))
    hello = await conn.read()
    assert hello.ok
    assert (hello.payload["node"], hello.payload["service"], hello.payload["proto"]) == (
        "node-a",
        "svc",
        0,
    )
    await conn.send(make_request(MSG_DESCRIBE))
    descr = await conn.read()
    assert descr.ok
    assert descr.payload["actions"][0]["id"] == "ping"
    assert descr.payload["actions"][0]["params"][0]["name"] == "target"
    await conn.send(make_request(MSG_GET_STATE))
    state = await conn.read()
    assert state.ok and state.payload == {"n": 1}
    await conn.send(make_request(MSG_COMMAND, {"action": "ping", "args": {"target": "x"}}))
    cmd = await conn.read()
    assert cmd.ok and cmd.payload == {"accepted": True}


@pytest.mark.parametrize("kind", ["no-auth", "wrong-token"])
async def test_tcp_unauthorized(kit, kind) -> None:
    srv = await kit.start([TcpEndpoint("127.0.0.1", 0)], token=TOKEN)
    conn = await tcp_conn(srv.endpoints[0].port)
    if kind == "no-auth":
        await conn.send(make_request(MSG_HELLO))
    else:
        await conn.send(make_request(MSG_AUTH, {"token": "wrong", "incarnation": "i"}))
    resp = await conn.read()
    assert resp.error_code() == "unauthorized"
    assert await conn.line() == b""


async def test_tcp_auth_repeat(kit) -> None:
    srv = await kit.start([TcpEndpoint("127.0.0.1", 0)], token=TOKEN)
    conn = await tcp_conn(srv.endpoints[0].port)
    first = await auth(conn)
    assert first.ok and first.payload == {"authenticated": True}
    second = await auth(conn)
    assert second.ok and second.payload == {"authenticated": True}


async def test_incarnation_change(kit) -> None:
    calls: list[str] = []
    srv = await kit.start(
        [TcpEndpoint("127.0.0.1", 0)], token=TOKEN, on_peer_connect=calls.append
    )
    conn1 = await tcp_conn(srv.endpoints[0].port)
    assert (await auth(conn1)).ok
    conn2 = await tcp_conn(srv.endpoints[0].port)
    assert (await auth(conn2, incarnation="inc-2")).ok
    assert await conn1.line() == b""
    assert calls == ["node-a"]
    conn3 = await tcp_conn(srv.endpoints[0].port)
    assert (await auth(conn3, incarnation="inc-2")).ok
    assert calls == ["node-a"]
    assert srv.connection_count == 2


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"action": "nope"}, ERR_UNKNOWN_ACTION),
        ({"action": "ping"}, ERR_BAD_REQUEST),
        ({"action": "ping", "args": "x"}, ERR_BAD_REQUEST),
    ],
)
async def test_command_errors(kit, unix_ep, payload, code) -> None:
    await kit.start([unix_ep])
    conn = await unix_conn(unix_ep)
    await conn.send(make_request(MSG_COMMAND, payload))
    resp = await conn.read()
    assert resp.error_code() == code


async def test_handler_crash(kit, unix_ep) -> None:
    await kit.start([unix_ep], Handler(fail=True))
    conn = await unix_conn(unix_ep)
    await conn.send(make_request(MSG_COMMAND, {"action": "ping", "args": {"target": "x"}}))
    resp = await conn.read()
    assert resp.error_code() == ERR_INTERNAL
    assert resp.error_message() == "внутренняя ошибка"


async def test_garbage_line(kit, unix_ep) -> None:
    srv = await kit.start([unix_ep])
    conn = await unix_conn(unix_ep)
    await conn.send(b"not json\n")
    resp = await conn.read()
    assert resp.error_code() == ERR_BAD_REQUEST
    await conn.send(make_request(MSG_HELLO))
    assert (await conn.read()).ok
    assert srv.connection_count == 1


async def test_router_first(kit, unix_ep) -> None:
    async def router(request: Envelope) -> Envelope | None:
        if request.type == MSG_HELLO:
            return make_response(request, {"via": "router"})
        return None

    await kit.start([unix_ep], router=router)
    conn = await unix_conn(unix_ep)
    await conn.send(make_request(MSG_HELLO))
    assert (await conn.read()).payload == {"via": "router"}
    await conn.send(make_request(MSG_GET_STATE))
    assert (await conn.read()).payload == {"n": 1}


async def test_broadcast(kit, unix_ep) -> None:
    srv = await kit.start([unix_ep, TcpEndpoint("127.0.0.1", 0)], token=TOKEN)
    assert srv.bound_endpoints[1].port > 0
    unix = await unix_conn(unix_ep)
    authed = await tcp_conn(srv.endpoints[1].port)
    assert (await auth(authed)).ok
    unauth = await tcp_conn(srv.endpoints[1].port)
    assert srv.connection_count == 3
    delivered = await srv.broadcast_event("node_up", {"x": 1})
    assert delivered == 2
    for conn in (unix, authed):
        env = await conn.read()
        assert env.type == MSG_EVENT
        assert env.payload["event"] == "node_up"
        assert env.payload["data"] == {"x": 1}
    with pytest.raises(TimeoutError):
        await unauth.read(timeout=0.3)
    await unix.send(make_request(MSG_HELLO))
    assert (await unix.read()).ok
