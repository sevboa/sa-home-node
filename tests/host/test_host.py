from __future__ import annotations

import asyncio
import ctypes
import os
import shutil
import signal
import socket
import subprocess
from pathlib import Path

import pytest

from sa_home_node.host import current, select
from sa_home_node.host.base import RESTART_EXIT_CODE, NodeUnit, install_stop_handlers
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


@pytest.fixture
def pick(monkeypatch):
    def pick(platform):
        if platform == "win32":
            monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
            monkeypatch.setattr(signal, "SIGBREAK", 21, raising=False)
            monkeypatch.setattr(signal, "CTRL_BREAK_EVENT", 1, raising=False)
        return select(platform)

    return pick


@pytest.mark.parametrize(
    ("platform", "kwargs", "signals", "terminated"),
    [
        ("linux", {}, (signal.SIGINT, signal.SIGTERM), ["terminate"]),
        ("win32", {"creationflags": 0x200}, (signal.SIGINT, signal.SIGTERM, 21), [("send_signal", 1)]),
    ],
)
def test_process_control(pick, platform, kwargs, signals, terminated):
    host, proc = pick(platform), FakeProc()
    host.terminate(proc)
    assert (host.spawn_kwargs(), host.stop_signals(), proc.calls) == (kwargs, signals, terminated)


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
    loop, cb = FakeLoop(), lambda: None
    remove = install_stop_handlers(loop, (signal.SIGINT, signal.SIGTERM), cb)
    assert loop.added == [(signal.SIGINT, cb), (signal.SIGTERM, cb)]
    remove()
    assert loop.removed == [signal.SIGINT, signal.SIGTERM]


def test_install_stop_handlers_fallback(monkeypatch):
    seen, calls = [], []

    def fake_signal(sig, handler):
        calls.append((sig, handler))
        return "old"

    monkeypatch.setattr(signal, "signal", fake_signal)
    remove = install_stop_handlers(FakeLoop(fail=True), (signal.SIGINT, signal.SIGTERM), lambda: seen.append(1))
    assert [sig for sig, _ in calls] == [signal.SIGINT, signal.SIGTERM]
    for _, handler in calls:
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
        ("win32", "suspend", ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]),
    ],
)
def test_power_argv(platform, action, expected):
    assert select(platform).power_argv(action) == expected
    with pytest.raises(ValueError):
        select(platform).power_argv("hibernate")


@pytest.mark.parametrize(("platform", "expected"), [("linux", 0), ("win32", RESTART_EXIT_CODE)])
def test_self_restart(platform, expected, monkeypatch):
    recorded = []
    monkeypatch.setattr(os, "execv", lambda prog, argv: recorded.append((prog, argv)), raising=False)
    assert select(platform).self_restart(["prog", "-x"]) == expected
    assert recorded == ([("prog", ["prog", "-x"])] if platform == "linux" else [])


class FakeSchedProc:
    def __init__(self, returncode):
        self.returncode = returncode

    async def communicate(self):
        return (b"ok\n", None)


@pytest.mark.parametrize(
    ("platform", "returncode", "expected"),
    [
        ("linux", 0, (False, "нет задачи обновления")),
        ("win32", 0, (True, "ok")),
        ("win32", 1, (False, "ok")),
        ("win32", None, (False, "нет")),
    ],
)
async def test_trigger_update_task(platform, returncode, expected, monkeypatch):
    seen = []

    async def fake_exec(*args, **kwargs):
        seen.append(args)
        if returncode is None:
            raise OSError("нет")
        return FakeSchedProc(returncode)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await select(platform).trigger_update_task() == expected
    expected_calls = [("schtasks", "/run", "/tn", "sa-home-node-autoupdate")] if platform == "win32" else []
    assert seen == expected_calls


@pytest.mark.parametrize(
    ("manager", "argv"),
    [
        ("apt", ["apt-get", "install", "-y"]),
        ("pacman", ["pacman", "-S", "--noconfirm"]),
        ("dnf", ["dnf", "install", "-y"]),
        ("zypper", ["zypper", "--non-interactive", "install"]),
        ("apk", ["apk", "add"]),
        ("brew", ["brew", "install"]),
    ],
)
def test_package_install_argv(manager, argv, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/x" if name == manager else None)
    assert select("linux").package_install_argv("pkg-a") == [*argv, "pkg-a"]
    assert select("win32").package_install_argv("pkg-a") is None


def test_package_install_argv_first_wins_and_none(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/x" if name in ("dnf", "brew") else None)
    assert select("linux").package_install_argv("pkg-a")[0] == "dnf"
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert select("linux").package_install_argv("pkg-a") is None


def test_elevate_argv():
    assert select("linux").elevate_argv(["id", "-u"]) == ["sudo", "id", "-u"]
    assert select("win32").elevate_argv(["id"]) is None


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        (
            "linux",
            NodeUnit(
                "systemd-user",
                Path("/home/user-a/.config/systemd/user/sa-home-node.service"),
                ["systemctl", "--user", "daemon-reload"],
            ),
        ),
        ("win32", NodeUnit("winsw", Path("/cfg/service/sa-home-node.xml"), None)),
    ],
)
def test_node_unit(platform, expected, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/user-a")))
    assert select(platform).node_unit(Path("/cfg/config.toml")) == expected
