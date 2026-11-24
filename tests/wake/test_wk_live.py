from __future__ import annotations

import asyncio
import json
import sys
from functools import partial
from pathlib import Path

import pytest
from support import wait_for

from sa_home_node import wol
from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.server import ProtoServer
from sa_home_node.proto.wire import ERR_BAD_REQUEST, Address, ProtoError
from sa_home_node.swarm.service import NodeService
from sa_home_node.wake.actions import local_wake_payload, register_wake

pytestmark = [pytest.mark.skipif(sys.platform != "linux", reason="unix"), pytest.mark.timeout(60)]

REF = json.loads((Path(__file__).parents[1] / "fixtures" / "wake" / "actions.json").read_text(encoding="utf-8"))
INFO = wol.LocalWakeInfo(mac="02:00:00:00:00:0a", ip="127.0.0.1", broadcast="127.255.255.255")


class Sink(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.packets: list[bytes] = []

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self.packets.append(data)


@pytest.fixture
async def wake_node(sock_dir, monkeypatch):
    sink = Sink()
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: sink, local_addr=("127.0.0.1", 0)
    )
    port = transport.get_extra_info("sockname")[1]
    monkeypatch.setattr(wol, "detect_local_wake_info", lambda: INFO)
    monkeypatch.setattr(
        wol, "send_magic_packet", partial(wol.send_magic_packet, broadcast="127.0.0.1", port=port, repeats=1)
    )
    service = NodeService("node-a", wake=local_wake_payload)
    register_wake(service)
    sock = f"{sock_dir}/node.sock"
    server = ProtoServer(sock, service)
    await server.start()
    client = ProtoClient(sock)
    await client.connect()
    try:
        yield client, sink
    finally:
        await client.close()
        await server.stop()
        transport.close()


async def test_send_wol_reaches_udp_socket(wake_node) -> None:
    client, sink = wake_node
    reply = await client.command("send_wol", {"mac": "AA-BB-CC-DD-EE-FF"})
    assert reply == {"sent": True, "mac": "aa:bb:cc:dd:ee:ff"}
    assert await wait_for(lambda: bool(sink.packets))
    assert sink.packets == [b"\xff" * 6 + bytes.fromhex("aabbccddeeff") * 16]
    assert len(sink.packets[0]) == 102


BAD_MAC = [c for c in REF["send_wol"] if c["name"] in ("bad_mac", "non_string_mac")]


@pytest.mark.parametrize("case", BAD_MAC, ids=lambda c: c["name"])
async def test_bad_mac_is_bad_request(wake_node, case) -> None:
    client, sink = wake_node
    with pytest.raises(ProtoError) as caught:
        await client.command("send_wol", case["args"])
    assert (caught.value.code, caught.value.message) == (ERR_BAD_REQUEST, case["message"])
    with pytest.raises(ProtoError) as missing:
        await client.command("send_wol", {})
    assert missing.value.code == ERR_BAD_REQUEST
    assert sink.packets == []


async def test_describe_carries_wake_and_action(wake_node) -> None:
    client, _ = wake_node
    description = await client.describe(dst=Address(node="node-a", service="node"))
    assert description.info.wake == {"mac": INFO.mac, "ip": INFO.ip, "broadcast": INFO.broadcast}
    assert [a.to_dict() for a in description.actions if a.id == "send_wol"] == [REF["send_wol_spec"]]
