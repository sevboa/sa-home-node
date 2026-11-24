from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from sa_home_node import wol
from sa_home_node.proto.wire import ProtoError
from sa_home_node.swarm.service import NodeService
from sa_home_node.wake import actions

REF = json.loads((Path(__file__).parent.parent / "fixtures/wake/actions.json").read_text(encoding="utf-8"))
INFO = wol.LocalWakeInfo(mac="02:00:00:00:00:0a", ip="192.0.2.10", broadcast="192.0.2.255")


def test_constants_match_reference() -> None:
    assert REF["constants"] == {"action": actions.ACTION_SEND_WOL, "title": actions.SEND_WOL_TITLE}


@pytest.mark.parametrize("case", REF["local_wake_payload"], ids=lambda c: c["name"])
def test_local_wake_payload(monkeypatch: pytest.MonkeyPatch, case: dict) -> None:
    monkeypatch.setattr(wol, "detect_local_wake_info", lambda: INFO if case["payload"] else None)
    assert actions.local_wake_payload() == case["payload"]


@pytest.mark.parametrize("name", ["with_wake", "without_wake"])
def test_describe_fragment(name: str) -> None:
    expected = REF["info_payload"][name]
    service = NodeService("node-a", wake=lambda: expected["wake"])
    actions.register_wake(service)
    description = service.describe()
    assert description.info.wake == expected["wake"]
    assert [a.to_dict() for a in description.actions if a.id == "send_wol"] == [REF["send_wol_spec"]]


@pytest.mark.parametrize("case", REF["send_wol"], ids=lambda c: c["name"])
async def test_send_wol_matches_reference(monkeypatch: pytest.MonkeyPatch, case: dict) -> None:
    iface = case["name"] != "ok_without_iface"
    monkeypatch.setattr(wol, "detect_local_wake_info", lambda: INFO if iface else None)
    sends = []

    def send(mac: str, *args: object, **kwargs: object) -> None:
        sends.append({"mac": mac, "args": list(args), "kwargs": kwargs})
        if case["name"] == "send_error":
            raise OSError("сеть недоступна")

    monkeypatch.setattr(wol, "send_magic_packet", send)
    service = NodeService("node-a")
    actions.register_wake(service)
    if "error" in case:
        with pytest.raises(ProtoError) as caught:
            await service.run_command("send_wol", case["args"])
        assert caught.value.code == case["code"]
        assert str(caught.value) == case["message"] or caught.value.message == case["message"]
    else:
        assert await service.run_command("send_wol", case["args"]) == case["value"]
    assert sends == case["sends"]


async def test_packet_reaches_loopback_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wol, "detect_local_wake_info", lambda: wol.LocalWakeInfo("02:00:00:00:00:0a", "127.0.0.1", "127.255.255.255"))
    real = wol.send_magic_packet
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(2)
        port = receiver.getsockname()[1]
        monkeypatch.setattr(wol, "send_magic_packet", lambda mac, **kw: real(mac, broadcast="127.0.0.1", port=port, **kw))
        service = NodeService("node-a")
        actions.register_wake(service)
        await service.run_command("send_wol", {"mac": "aa:bb:cc:dd:ee:ff"})
        assert receiver.recv(200) == wol.build_magic_packet("aa:bb:cc:dd:ee:ff")
