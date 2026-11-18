from __future__ import annotations

import socket
import types
from pathlib import Path

import psutil
import pytest

from sa_home_node import host
from sa_home_node.proto import endpoints


class _FakeHost:
    def __init__(self, unix_sockets: bool):
        self.name = "test"
        self.unix_sockets = unix_sockets

    def enable_tcp_keepalive(self, sock: socket.socket) -> None:
        pass

    def system_uptime_s(self) -> float | None:
        return None


def _fake_addr(address: str, broadcast: str | None = None) -> types.SimpleNamespace:
    return types.SimpleNamespace(family=socket.AF_INET, address=address, broadcast=broadcast)


@pytest.mark.parametrize(
    "value,expected",
    [
        (Path("./data/node.sock"), endpoints.UnixEndpoint(Path("./data/node.sock"))),
        ("unix:./data/x.sock", endpoints.UnixEndpoint(Path("./data/x.sock"))),
        ("unix:///tmp/x.sock", endpoints.UnixEndpoint(Path("/tmp/x.sock"))),
        ("tcp://127.0.0.1:8710", endpoints.TcpEndpoint("127.0.0.1", 8710)),
        ("tcp://node-a.example:8710", endpoints.TcpEndpoint("node-a.example", 8710)),
        ("tcp://[::1]:8710", endpoints.TcpEndpoint("::1", 8710)),
    ],
)
def test_parse_endpoint_valid(value, expected):
    result = endpoints.parse_endpoint(value)
    assert result == expected
    if isinstance(value, str) and value.startswith("tcp://") and not value.startswith("tcp://["):
        assert str(result) == value


def test_parse_endpoint_endpoint_passthrough():
    ep = endpoints.TcpEndpoint("127.0.0.1", 8710)
    assert endpoints.parse_endpoint(ep) is ep


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "tcp://127.0.0.1",
        "tcp://:99",
        "tcp://h:0",
        "tcp://h:65536",
        "tcp://h:port",
        "unix:",
    ],
)
def test_parse_endpoint_invalid(value):
    with pytest.raises(ValueError):
        endpoints.parse_endpoint(value)


def test_resolve_endpoint_relative_unix(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    ep = endpoints.resolve_endpoint("unix:./data/node.sock", base_dir=base)
    assert isinstance(ep, endpoints.UnixEndpoint)
    assert ep.path == base / "data" / "node.sock"


def test_resolve_endpoint_absolute_unix(tmp_path):
    ep = endpoints.resolve_endpoint("unix:///tmp/x.sock", base_dir=tmp_path)
    assert isinstance(ep, endpoints.UnixEndpoint)
    assert ep.path == Path("/tmp/x.sock")


def test_resolve_endpoint_tcp_unchanged():
    ep = endpoints.resolve_endpoint("tcp://127.0.0.1:8710", base_dir=Path("/tmp"))
    assert isinstance(ep, endpoints.TcpEndpoint)
    assert ep.host == "127.0.0.1"
    assert ep.port == 8710


def test_resolve_endpoint_no_base_dir():
    ep = endpoints.resolve_endpoint("unix:./data/node.sock", base_dir=None)
    assert isinstance(ep, endpoints.UnixEndpoint)
    assert ep.path == Path("./data/node.sock")


def test_unix_endpoint_requires_unix_sockets(monkeypatch):
    monkeypatch.setattr(host, "current", lambda: _FakeHost(unix_sockets=False))

    with pytest.raises(ValueError) as exc:
        endpoints.parse_endpoint("./data/node.sock")
    assert "tcp://127.0.0.1" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        endpoints.parse_endpoint("unix:/tmp/x.sock")
    assert "tcp://127.0.0.1" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        endpoints.parse_endpoint(Path("./data/node.sock"))
    assert "tcp://127.0.0.1" in str(exc.value)

    ep = endpoints.parse_endpoint("tcp://127.0.0.1:8710")
    assert isinstance(ep, endpoints.TcpEndpoint)


@pytest.mark.parametrize(
    ("value", "rank"),
    [
        ("/tmp/x.sock", endpoints.RANK_LOCAL),
        ("tcp://192.0.2.10:8710", endpoints.RANK_LAN),
        ("tcp://100.64.0.1:8710", endpoints.RANK_OVERLAY),
        ("tcp://8.8.8.8:8710", endpoints.RANK_OTHER),
    ],
)
def test_endpoint_rank(value, rank):
    assert endpoints.endpoint_rank(endpoints.parse_endpoint(value)) == rank
    assert endpoints.RANK_LAN < endpoints.RANK_OVERLAY < endpoints.RANK_OTHER


@pytest.mark.parametrize(
    "ep,expected",
    [
        (endpoints.UnixEndpoint(Path("/tmp/x.sock")), True),
        (endpoints.TcpEndpoint("127.0.0.1", 8710), True),
        (endpoints.TcpEndpoint("localhost", 8710), True),
        (endpoints.TcpEndpoint("192.0.2.10", 8710), False),
    ],
)
def test_is_loopback(ep, expected):
    assert endpoints.is_loopback(ep) == expected


def test_is_unspecified():
    assert endpoints.is_unspecified(endpoints.TcpEndpoint("0.0.0.0", 8710)) is True
    assert endpoints.is_unspecified(endpoints.TcpEndpoint("127.0.0.1", 8710)) is False
    assert endpoints.is_unspecified(endpoints.UnixEndpoint(Path("/tmp/x.sock"))) is False


@pytest.mark.parametrize(
    ("name", "virtual"),
    [
        ("br0", False),
        ("enp1s0", False),
        ("ovl0", False),
        ("br-1a2b3c4d", True),
        ("vEthernet (WSL)", True),
        ("veth12", True),
        ("virbr0", True),
    ],
)
def test_is_virtual_iface(name, virtual):
    assert endpoints.is_virtual_iface(name) is virtual


def test_local_ipv4_addresses(monkeypatch):
    fake_addrs = {
        "eth0": [_fake_addr("192.0.2.10", "192.0.2.255"), _fake_addr("192.0.2.10")],
        "ovl0": [_fake_addr("100.64.0.1", "100.64.0.1"), _fake_addr("169.254.0.5")],
        "lo": [_fake_addr("127.0.0.1")],
        "docker0": [_fake_addr("172.17.0.1")],
        "br-1a2b3c4d": [_fake_addr("172.18.0.1")],
        "vEthernet (WSL)": [_fake_addr("172.19.0.1")],
    }
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: fake_addrs)

    assert endpoints.local_ipv4_addresses() == ["192.0.2.10", "100.64.0.1"]


def test_local_broadcast_addresses(monkeypatch):
    fake_addrs = {
        "eth0": [_fake_addr("192.0.2.10", "192.0.2.255")],
        "ovl0": [_fake_addr("100.64.0.1", "100.64.0.1")],
        "docker0": [_fake_addr("172.17.0.1", "172.17.0.255")],
    }
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: fake_addrs)

    assert endpoints.local_broadcast_addresses() == ["192.0.2.255", "100.64.0.1"]


def test_advertisable_filters_and_sorts(monkeypatch):
    monkeypatch.setattr(endpoints, "local_ipv4_addresses", lambda: ["192.0.2.10", "100.64.0.1"])

    eps = [
        endpoints.UnixEndpoint(Path("/tmp/x.sock")),
        endpoints.TcpEndpoint("127.0.0.1", 8710),
        endpoints.TcpEndpoint("100.64.0.1", 8710),
        endpoints.TcpEndpoint("0.0.0.0", 8710),
    ]
    assert endpoints.advertisable(eps) == ["tcp://192.0.2.10:8710", "tcp://100.64.0.1:8710"]


def test_merge_endpoints():
    assert endpoints.merge_endpoints("a", ["b", "a", "", 1]) == ["a", "b"]
    assert endpoints.merge_endpoints("", None) == []
    assert endpoints.merge_endpoints("first", ["second", "third"]) == ["first", "second", "third"]
    assert endpoints.merge_endpoints("only", []) == ["only"]
