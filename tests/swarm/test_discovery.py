from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from support import wait_for

from sa_home_node.proto.endpoints import local_broadcast_addresses
from sa_home_node.swarm.config import DiscoverySection
from sa_home_node.swarm.discovery import (
    MAX_DATAGRAM_BYTES,
    MAX_REPLY_ENDPOINTS,
    SwarmDiscovery,
    encode,
    make_query,
    make_reply,
    next_interval,
    parse,
    sign,
    usable_endpoints,
)
from sa_home_node.swarm.link import PeerLink
from sa_home_node.swarm.router import NodeRouter

TOKEN = "test-token"
OTHER_TOKEN = "other-token"
NONCE = "0000000000000000000000000000000b"
ADVERTISE = ["tcp://192.0.2.10:8710", "tcp://192.0.2.11:8710"]
FIXTURE = Path(__file__).parent.parent / "fixtures" / "wire" / "discovery.ndjson"

FIXTURE_BODIES = [
    make_query("node-a", NONCE),
    make_reply(
        "node-b",
        NONCE,
        ["tcp://192.0.2.20:8710", "tcp://192.0.2.21:8710"],
        kind="server",
    ),
]
QUERY_SIGNED = {**make_query("node-a", NONCE), "sig": sign(TOKEN, make_query("node-a", NONCE))}
TAMPER = {"v": 1, "t": "x", "node": "node-c", "nonce": "ff", "kind": "x", "endpoints": []}


def fixture_lines() -> list[str]:
    return [line for line in FIXTURE.read_text().split("\n") if line]


def alive_link(name: str, endpoint: str = "tcp://192.0.2.30:8710") -> PeerLink:
    link = PeerLink(name, endpoint)
    link._client = object()
    return link


@pytest.mark.parametrize(
    ("line", "body"),
    [*zip(fixture_lines(), FIXTURE_BODIES, strict=True)],
    ids=["query", "reply"],
)
async def test_fixture_line(line: str, body: dict) -> None:
    assert encode(TOKEN, body) == line.encode()
    assert parse(line.encode(), TOKEN) == {**body, "sig": sign(TOKEN, body)}


@pytest.mark.parametrize(
    ("payload", "token"),
    [
        (encode(TOKEN, make_query("node-a", NONCE)), OTHER_TOKEN),
        (json.dumps({**QUERY_SIGNED, "node": "node-c"}, separators=(",", ":")).encode(), TOKEN),
        (json.dumps({**QUERY_SIGNED, "v": 1}, separators=(",", ":")).encode(), TOKEN),
        (b"not-json", TOKEN),
        (encode(TOKEN, make_query("node-a", NONCE)) + b"x" * (MAX_DATAGRAM_BYTES + 1), TOKEN),
        (encode(TOKEN, make_query("node-a", NONCE)), ""),
        (b"not-json", ""),
    ],
    ids=["чужой-токен", "подделка", "версия", "мусор", "длинная", "пустой-токен", "пустой-токен-мусор"],
)
async def test_parse_rejects(payload: bytes, token: str) -> None:
    assert parse(payload, token) is None


@pytest.mark.parametrize(
    ("body", "keys"),
    [
        (make_query("node-a", NONCE), list(TAMPER) + ["sig"]),
        (
            make_reply("node-b", NONCE, ["tcp://192.0.2.20:8710"], kind="server"),
            list(TAMPER) + ["sig"],
        ),
    ],
    ids=["query", "reply"],
)
async def test_signature_covers(body: dict, keys: list) -> None:
    signed = {**body, "sig": sign(TOKEN, body)}
    for key in keys:
        copy = dict(signed)
        copy[key] = "00" * 32 if key == "sig" else TAMPER[key]
        assert parse(json.dumps(copy, separators=(",", ":")).encode(), TOKEN) is None


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], []),
        ("не-список", []),
        ({"a": 1}, []),
        (None, []),
        (5, []),
        (
            ["", 5, "мусор", "tcp://127.0.0.1:1", "tcp://192.0.2.3:8710", "tcp://192.0.2.3:8710"],
            ["tcp://192.0.2.3:8710"],
        ),
        (
            [f"tcp://192.0.2.{i}:8710" for i in range(MAX_REPLY_ENDPOINTS + 2)],
            [f"tcp://192.0.2.{i}:8710" for i in range(MAX_REPLY_ENDPOINTS)],
        ),
    ],
    ids=["пусто", "не-список", "словарь", "none", "число", "мусор", "обрезка"],
)
async def test_usable_endpoints(values: object, expected: list) -> None:
    assert usable_endpoints(values) == expected


@pytest.mark.parametrize(
    ("links", "expected"),
    [
        ([], DiscoverySection().active_interval_s),
        ([PeerLink("node-b", "tcp://192.0.2.20:8710")], DiscoverySection().active_interval_s),
        ([alive_link("node-c")], DiscoverySection().idle_interval_s),
    ],
    ids=["нет-пиров", "мёртвый", "живые"],
)
async def test_next_interval(links: list, expected: float) -> None:
    assert next_interval(links, DiscoverySection()) == expected


async def beacon(node_id: str, router: NodeRouter | None = None, **kw) -> tuple:
    kw.setdefault("token", TOKEN)
    kw.setdefault("advertise", lambda: ADVERTISE)
    kw.setdefault("port", 0)
    router = router if router is not None else NodeRouter(node_id)
    joined: list[str] = []

    async def join(endpoint: str) -> dict:
        joined.append(endpoint)
        return {"peers_added": [endpoint]}

    disc = SwarmDiscovery(node_id, router, join=join, **kw)
    await disc.start()
    return disc, joined


async def test_query_joins_found() -> None:
    b, b_joined = await beacon(
        "node-b",
        advertise=lambda: ["tcp://192.0.2.20:8710", "tcp://192.0.2.21:8710"],
        node_kind="server",
    )
    try:
        a, joined = await beacon("node-a", targets=lambda: [("127.0.0.1", b.port)])
        try:
            assert await wait_for(lambda: bool(joined))
            assert joined == ["tcp://192.0.2.20:8710"]
            assert a.state()["peers_found"] == ["node-b"]
            assert a.state()["replies_seen"] == 1
            assert b_joined == []
        finally:
            await a.stop()
    finally:
        await b.stop()


async def test_foreign_nonce_ignored() -> None:
    a, joined = await beacon("node-a", targets=list)
    b, _ = await beacon("node-b", advertise=list)
    try:
        b._transport.sendto(
            encode(TOKEN, make_reply("node-c", "ff" * 32, ADVERTISE)),
            ("127.0.0.1", a.port),
        )
        await asyncio.sleep(0.1)
        assert joined == []
        assert a.state()["replies_seen"] == 0
    finally:
        await a.stop()
        await b.stop()


async def test_own_echo_ignored() -> None:
    a, joined = await beacon("node-a", targets=list)
    try:
        a.query_once()
        nonce = a._nonces[-1]
        a._transport.sendto(
            encode(TOKEN, make_reply("node-a", nonce, ADVERTISE)),
            ("127.0.0.1", a.port),
        )
        await asyncio.sleep(0.1)
        assert joined == []
        assert a.state()["replies_seen"] == 0
    finally:
        await a.stop()


async def test_dead_peer_readdressed(monkeypatch) -> None:
    link = PeerLink("node-b", "tcp://192.0.2.20:8710")
    learned: list = []
    reasons: list = []
    monkeypatch.setattr(link, "learn_endpoints", lambda advertised: learned.append(list(advertised)))
    monkeypatch.setattr(link, "reconnect_now", lambda reason: reasons.append(reason))
    router = NodeRouter("node-a", peers={"node-b": link})
    a, joined = await beacon("node-a", router=router, targets=list)
    b, _ = await beacon("node-b", advertise=list)
    try:
        b._transport.sendto(
            encode(TOKEN, make_reply("node-b", a._nonces[0], ["tcp://192.0.2.21:8710"])),
            ("127.0.0.1", a.port),
        )
        assert await wait_for(lambda: bool(learned))
        assert learned == [["tcp://192.0.2.21:8710"]]
        assert "tcp://192.0.2.21:8710" in reasons[0]
        assert joined == []
    finally:
        await a.stop()
        await b.stop()


async def test_alive_peer_untouched(monkeypatch) -> None:
    link = alive_link("node-b")
    learned: list = []
    reasons: list = []
    monkeypatch.setattr(link, "learn_endpoints", lambda advertised: learned.append(list(advertised)))
    monkeypatch.setattr(link, "reconnect_now", lambda reason: reasons.append(reason))
    router = NodeRouter("node-a", peers={"node-b": link})
    a, joined = await beacon("node-a", router=router, targets=list)
    b, _ = await beacon("node-b", advertise=list)
    try:
        b._transport.sendto(
            encode(TOKEN, make_reply("node-b", a._nonces[0], ["tcp://192.0.2.21:8710"])),
            ("127.0.0.1", a.port),
        )
        await asyncio.sleep(0.1)
        assert learned == []
        assert reasons == []
        assert link.endpoints == ["tcp://192.0.2.30:8710"]
        assert joined == []
    finally:
        await a.stop()
        await b.stop()


async def test_foreign_token_no_reply(monkeypatch) -> None:
    a, joined = await beacon("node-a", targets=list)
    try:
        sent: list = []
        monkeypatch.setattr(a._transport, "sendto", lambda packet, addr: sent.append(addr))
        b, _ = await beacon("node-b", advertise=list)
        try:
            b._transport.sendto(
                encode(OTHER_TOKEN, make_query("node-b", "ff" * 32)),
                ("127.0.0.1", a.port),
            )
            await asyncio.sleep(0.1)
            assert sent == []
            assert joined == []
        finally:
            await b.stop()
    finally:
        await a.stop()


async def test_busy_port() -> None:
    a, _ = await beacon("node-a", targets=list)
    try:
        b, _ = await beacon("node-a", port=a.port, targets=list)
        try:
            assert b.running is False
        finally:
            await b.stop()
    finally:
        await a.stop()


@pytest.mark.parametrize(
    ("kw",),
    [
        ({"cfg": DiscoverySection(enabled=False)},),
        ({"token": ""},),
    ],
    ids=["выключен", "пустой-токен"],
)
async def test_not_started(kw: dict) -> None:
    disc, _ = await beacon("node-a", **kw)
    try:
        assert disc.running is False
    finally:
        await disc.stop()


async def test_query_targets_swarm_port(monkeypatch) -> None:
    disc, joined = await beacon(
        "node-a",
        cfg=DiscoverySection(port=32167),
        port=0,
        advertise=lambda: ADVERTISE,
    )
    try:
        assert disc.port != 32167
        sent: list = []
        monkeypatch.setattr(disc._transport, "sendto", lambda packet, addr: sent.append(addr))
        disc.query_once()
        assert sent
        assert all(addr[1] == 32167 for addr in sent)
        assert {addr[0] for addr in sent} <= set(local_broadcast_addresses()) | {"255.255.255.255"}
        assert joined == []
    finally:
        await disc.stop()
