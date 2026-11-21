from __future__ import annotations

import asyncio
import ctypes
import os
import signal
import socket
import subprocess

import pytest

from sa_home_node.host import current, select
from sa_home_node.host.base import RESTART_EXIT_CODE, install_stop_handlers
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
    assert host.update_via_task is not unix


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


class FakeProc:
    def __init__(self):
        self.calls = []

    def terminate(self):
        self.calls.append("terminate")

    def send_signal(self, sig):
        self.calls.append(("send_signal", sig))


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("linux", {}), ("win32", {"creationflags": 0x200})],
)
def test_spawn_kwargs(platform, expected, monkeypatch):
    if platform == "win32":
        monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    assert select(platform).spawn_kwargs() == expected


@pytest.mark.parametrize(
    ("platform", "sigbreak"),
    [("linux", None), ("win32", 21)],
)
def test_stop_signals(platform, sigbreak, monkeypatch):
    if platform == "win32":
        monkeypatch.setattr(signal, "SIGBREAK", 21, raising=False)
    expected = (signal.SIGINT, signal.SIGTERM)
    if sigbreak is not None:
        expected += (sigbreak,)
    assert select(platform).stop_signals() == expected


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("linux", ["terminate"]), ("win32", [("send_signal", 1)])],
)
def test_terminate(platform, expected, monkeypatch):
    if platform == "win32":
        monkeypatch.setattr(signal, "CTRL_BREAK_EVENT", 1, raising=False)
    proc = FakeProc()
    select(platform).terminate(proc)
    assert proc.calls == expected


class FakeLoop:
    def __init__(self, fail=False):
        self.fail = fail
        self.added = []
        self.removed = []

    def add_signal_handler(self, sig, cb):
        if self.fail:
            raise NotImplementedError
        self.added.append((sig, cb))

    def remove_signal_handler(self, sig):
        self.removed.append(sig)


def test_install_stop_handlers_loop():
    loop = FakeLoop()
    seen = []

    def cb():
        seen.append(1)

    remove = install_stop_handlers(loop, (signal.SIGINT, signal.SIGTERM), cb)
    assert loop.added == [(signal.SIGINT, cb), (signal.SIGTERM, cb)]
    remove()
    assert loop.removed == [signal.SIGINT, signal.SIGTERM]


def test_install_stop_handlers_fallback(monkeypatch):
    loop = FakeLoop(fail=True)
    seen = []
    calls = []

    def fake_signal(sig, handler):
        calls.append((sig, handler))
        return "old"

    monkeypatch.setattr(signal, "signal", fake_signal)
    remove = install_stop_handlers(loop, (signal.SIGINT, signal.SIGTERM), lambda: seen.append(1))
    handlers = [calls[0][1], calls[1][1]]
    assert calls == [(signal.SIGINT, handlers[0]), (signal.SIGTERM, handlers[1])]
    for handler in handlers:
        handler("x")
    assert seen == [1, 1]
    calls.clear()
    assert remove() is None
    assert calls == [(signal.SIGINT, "old"), (signal.SIGTERM, "old")]


@pytest.mark.parametrize(
    ("platform", "action", "expected"),
    [
        ("linux", "poweroff", ["systemctl", "poweroff"]),
        ("linux", "reboot", ["systemctl", "reboot"]),
        ("linux", "suspend", ["systemctl", "suspend"]),
        ("win32", "poweroff", ["shutdown", "/s", "/t", "5"]),
        ("win32", "reboot", ["shutdown", "/r", "/t", "5"]),
        (
            "win32",
            "suspend",
            ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        ),
    ],
)
def test_power_argv(platform, action, expected):
    assert select(platform).power_argv(action) == expected


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_power_argv_unknown(platform):
    with pytest.raises(ValueError):
        select(platform).power_argv("hibernate")


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("linux", 0), ("win32", RESTART_EXIT_CODE)],
)
def test_self_restart(platform, expected, monkeypatch):
    recorded = []

    def fake_execv(prog, argv):
        recorded.append((prog, argv))

    if platform == "linux":
        monkeypatch.setattr(os, "execv", fake_execv, raising=False)
    host = select(platform)
    assert host.self_restart(["prog", "-x"]) == expected
    if platform == "linux":
        assert recorded == [("prog", ["prog", "-x"])]


async def test_trigger_update_task_linux():
    assert await select("linux").trigger_update_task() == (False, "нет задачи обновления")


class FakeSchedProc:
    def __init__(self, returncode):
        self.returncode = returncode

    async def communicate(self):
        return (b"ok\n", None)


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [(0, (True, "ok")), (1, (False, "ok"))],
)
async def test_trigger_update_task_windows(returncode, expected, monkeypatch):
    seen = []

    async def fake_exec(*args, **kwargs):
        seen.append(args)
        return FakeSchedProc(returncode)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await select("win32").trigger_update_task() == expected
    assert seen == [("schtasks", "/run", "/tn", "sa-home-node-autoupdate")]


async def test_trigger_update_task_windows_error(monkeypatch):
    async def fake_exec(*args, **kwargs):
        raise OSError("нет")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await select("win32").trigger_update_task() == (False, "нет")
