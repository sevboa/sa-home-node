from __future__ import annotations

import ctypes
import socket

import pytest

from sa_home_node.host import current, select
from sa_home_node.host.linux import LinuxHost
from sa_home_node.host.windows import WindowsHost


class FakeSock:
    def __init__(self):
        self.calls = []

    def setsockopt(self, *args):
        self.calls.append(("setsockopt", *args))

    def ioctl(self, *args):
        self.calls.append(("ioctl", *args))


def linux_calls():
    return [
        ("setsockopt", socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        ("setsockopt", socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT, 30000),
        ("setsockopt", socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 20),
        ("setsockopt", socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10),
        ("setsockopt", socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3),
    ]


def windows_calls():
    return [
        ("setsockopt", socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
        (
            "ioctl",
            0x1234,
            (1, 20000, 10000),
        ),
        ("setsockopt", socket.IPPROTO_TCP, 5, 30),
    ]


@pytest.mark.parametrize(
    ("platform", "cls", "unix"),
    [("linux", LinuxHost, True), ("win32", WindowsHost, False)],
)
def test_select(platform, cls, unix):
    host = select(platform)
    assert type(host) is cls
    assert host.unix_sockets is unix


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("linux", linux_calls), ("win32", windows_calls)],
)
def test_keepalive(platform, expected, monkeypatch):
    if platform == "win32":
        monkeypatch.setattr(socket, "SIO_KEEPALIVE_VALS", 0x1234, raising=False)
    sock = FakeSock()
    select(platform).enable_tcp_keepalive(sock)
    assert sock.calls == expected()


def test_linux_uptime(tmp_path, monkeypatch):
    host = LinuxHost()
    path = tmp_path / "uptime"
    path.write_text("12345.67 100\n")
    monkeypatch.setattr(LinuxHost, "uptime_path", str(path))
    assert host.system_uptime_s() == 12345.67
    monkeypatch.setattr(LinuxHost, "uptime_path", str(tmp_path / "missing"))
    assert host.system_uptime_s() is None


def test_windows_uptime(monkeypatch):
    class FakeTick:
        def GetTickCount64(self):
            return 150000

    monkeypatch.setattr(
        ctypes, "windll", type("W", (), {"kernel32": FakeTick()}), raising=False
    )
    assert select("win32").system_uptime_s() == 150.0


def test_current_cached():
    assert current() is current()
