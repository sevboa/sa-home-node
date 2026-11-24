from __future__ import annotations

import socket
from collections import namedtuple

import pytest

from sa_home_node import wol

MAC = "aa:bb:cc:dd:ee:ff"

_Snic = namedtuple("snic", ["family", "address", "netmask", "broadcast", "ptp"])


@pytest.mark.parametrize(
    "raw",
    [
        "aa:bb:cc:dd:ee:ff",
        "AA:BB:CC:DD:EE:FF",
        "aa-bb-cc-dd-ee-ff",
        "aabb.ccdd.eeff",
        "aabbccddeeff",
        "  AA-bb-CC-dd-EE-ff  ",
    ],
)
def test_normalize_mac_variants(raw: str):
    assert wol.normalize_mac(raw) == MAC


@pytest.mark.parametrize("raw", ["", "aa:bb:cc", "aa:bb:cc:dd:ee:fg", "слово", "aabbccddeeff00"])
def test_normalize_mac_rejects_garbage(raw: str):
    with pytest.raises(ValueError):
        wol.normalize_mac(raw)


def test_build_magic_packet_layout():
    packet = wol.build_magic_packet(MAC)
    mac_bytes = bytes.fromhex("aabbccddeeff")
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:] == mac_bytes * 16


def test_send_magic_packet_over_udp():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as recv:
        recv.bind(("127.0.0.1", 0))
        recv.settimeout(2)
        _, port = recv.getsockname()
        wol.send_magic_packet(MAC, broadcast="127.0.0.1", port=port, repeats=2)
        data, _ = recv.recvfrom(1024)
    assert data == wol.build_magic_packet(MAC)


@pytest.mark.parametrize(
    ("ip", "prefix", "broadcast"),
    [
        ("192.168.0.100", "255.255.255.0", "192.168.0.255"),
        ("10.1.2.3", "255.255.0.0", "10.1.255.255"),
        ("192.168.1.5", "255.255.255.252", "192.168.1.7"),
    ],
)
def test_broadcast_for_netmask(monkeypatch: pytest.MonkeyPatch, ip: str, prefix: str, broadcast: str):
    monkeypatch.setattr(wol, "_default_route_ip", lambda: ip)
    monkeypatch.setattr(
        wol.psutil,
        "net_if_addrs",
        lambda: {
            "enp1s0": [
                _Snic(socket.AF_INET, ip, prefix, None, None),
                _Snic(wol.psutil.AF_LINK, "7c:83:34:b4:59:ac", None, None, None),
            ]
        },
    )
    info = wol.detect_local_wake_info()
    assert info is not None
    assert info.broadcast == broadcast


def test_detect_none_without_netmask(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(wol, "_default_route_ip", lambda: "192.168.0.100")
    monkeypatch.setattr(
        wol.psutil,
        "net_if_addrs",
        lambda: {
            "enp1s0": [
                _Snic(socket.AF_INET, "192.168.0.100", None, None, None),
                _Snic(wol.psutil.AF_LINK, "7c:83:34:b4:59:ac", None, None, None),
            ]
        },
    )
    assert wol.detect_local_wake_info() is None


@pytest.mark.parametrize("name", ["docker0", "veth12", "br-ab", "virbr0", "wg0", "tun0", "VMware1"])
def test_virtual_names_rejected(monkeypatch: pytest.MonkeyPatch, name: str):
    _mock_lan(monkeypatch, name)
    assert wol.detect_local_wake_iface() is None


def test_is_wireless_by_name_without_sysfs(monkeypatch: pytest.MonkeyPatch):
    assert wol._is_wireless("Wi-Fi 2") is True
    assert wol._is_wireless("Ethernet 3") is False


@pytest.mark.parametrize("code", [0, 1])
async def test_ping_host_uses_host_argv(monkeypatch: pytest.MonkeyPatch, code: int):
    seen = []

    class Proc:
        async def wait(self):
            return code

    async def fake_exec(*argv, **kwargs):
        seen.append(list(argv))
        return Proc()

    monkeypatch.setattr(wol.asyncio, "create_subprocess_exec", fake_exec)
    assert await wol.ping_host("192.0.2.1") is (code == 0)
    assert seen == [wol.current_host().ping_argv("192.0.2.1")]


async def test_wait_host_up_returns_elapsed(monkeypatch: pytest.MonkeyPatch):
    responses = iter([False, False, True])

    async def fake_ping(ip: str) -> bool:
        return next(responses)

    monkeypatch.setattr(wol, "ping_host", fake_ping)
    elapsed = await wol.wait_host_up("192.0.2.1", timeout_s=10, interval_s=0.01)
    assert elapsed is not None


async def test_wait_host_up_timeout(monkeypatch: pytest.MonkeyPatch):
    async def fake_ping(ip: str) -> bool:
        return False

    monkeypatch.setattr(wol, "ping_host", fake_ping)
    assert await wol.wait_host_up("192.0.2.1", timeout_s=0.05, interval_s=0.01) is None


def test_send_magic_packet_binds_to_interface():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as recv:
        recv.bind(("127.0.0.1", 0))
        recv.settimeout(2)
        _, port = recv.getsockname()
        wol.send_magic_packet(MAC, broadcast="127.0.0.1", port=port, repeats=1, bind_ip="127.0.0.1")
        data, addr = recv.recvfrom(1024)
    assert addr[0] == "127.0.0.1"
    assert data == wol.build_magic_packet(MAC)


def _mock_lan(monkeypatch: pytest.MonkeyPatch, iface: str, ip: str = "192.168.0.100") -> None:
    monkeypatch.setattr(wol, "_default_route_ip", lambda: ip)
    monkeypatch.setattr(
        wol.psutil,
        "net_if_addrs",
        lambda: {
            iface: [
                _Snic(socket.AF_INET, ip, "255.255.255.0", "192.168.0.255", None),
                _Snic(wol.psutil.AF_LINK, "7c:83:34:b4:59:ac", None, None, None),
            ]
        },
    )


def test_detect_local_wake_info_on_ethernet(monkeypatch: pytest.MonkeyPatch):
    _mock_lan(monkeypatch, "enp1s0")
    info = wol.detect_local_wake_info()
    assert info == wol.LocalWakeInfo(
        mac="7c:83:34:b4:59:ac", ip="192.168.0.100", broadcast="192.168.0.255"
    )


def test_detect_local_wake_info_none_on_wifi(monkeypatch: pytest.MonkeyPatch):
    _mock_lan(monkeypatch, "wlp2s0")
    monkeypatch.setattr(wol, "_is_wireless", lambda name: True)
    assert wol.detect_local_wake_info() is None


def test_detect_local_wake_info_none_on_virtual_iface(monkeypatch: pytest.MonkeyPatch):
    _mock_lan(monkeypatch, "vpn0")
    monkeypatch.setattr(wol.netif, "is_tun", lambda name: name == "vpn0")
    assert wol.detect_local_wake_info() is None


def test_detect_local_wake_info_none_without_default_route(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(wol, "_default_route_ip", lambda: None)
    assert wol.detect_local_wake_info() is None


def test_detect_local_wake_iface_on_ethernet(monkeypatch: pytest.MonkeyPatch):
    _mock_lan(monkeypatch, "enp1s0")
    assert wol.detect_local_wake_iface() == "enp1s0"


def test_detect_local_wake_iface_none_on_virtual_iface(monkeypatch: pytest.MonkeyPatch):
    _mock_lan(monkeypatch, "vpn0")
    monkeypatch.setattr(wol.netif, "is_tun", lambda name: name == "vpn0")
    assert wol.detect_local_wake_iface() is None
