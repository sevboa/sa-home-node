from __future__ import annotations

import asyncio
import contextlib
import getpass
import os
import shutil
import signal
import socket
from pathlib import Path

from .base import (
    TCP_KEEPALIVE_COUNT,
    TCP_KEEPALIVE_IDLE_S,
    TCP_KEEPALIVE_INTERVAL_S,
    TCP_USER_TIMEOUT_S,
    NodeUnit,
    check_power_action,
)

PACKAGE_INSTALL_ARGV: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("apt", ("apt-get", "install", "-y")),
    ("pacman", ("pacman", "-S", "--noconfirm")),
    ("dnf", ("dnf", "install", "-y")),
    ("zypper", ("zypper", "--non-interactive", "install")),
    ("apk", ("apk", "add")),
    ("brew", ("brew", "install")),
)


NODE_UNIT_NAME = "sa-home-node.service"


class LinuxHost:
    name = "linux"
    unix_sockets = True
    update_via_task = False
    uptime_path = "/proc/uptime"

    def enable_tcp_keepalive(self, sock: socket.socket) -> None:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        user_timeout_opt = getattr(socket, "TCP_USER_TIMEOUT", None)
        if user_timeout_opt is not None:
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.IPPROTO_TCP, user_timeout_opt, TCP_USER_TIMEOUT_S * 1000)
        for opt_name, value in (
            ("TCP_KEEPIDLE", TCP_KEEPALIVE_IDLE_S),
            ("TCP_KEEPINTVL", TCP_KEEPALIVE_INTERVAL_S),
            ("TCP_KEEPCNT", TCP_KEEPALIVE_COUNT),
        ):
            opt = getattr(socket, opt_name, None)
            if opt is not None:
                with contextlib.suppress(OSError):
                    sock.setsockopt(socket.IPPROTO_TCP, opt, value)

    def system_uptime_s(self) -> float | None:
        try:
            with open(self.uptime_path) as f:
                return float(f.read().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    def spawn_kwargs(self) -> dict:
        return {}

    def power_argv(self, action: str) -> list[str]:
        check_power_action(action)
        return ["systemctl", action]

    def self_restart(self, argv: list[str]) -> int:
        os.execv(argv[0], argv)
        return 0

    async def trigger_update_task(self) -> tuple[bool, str]:
        return False, "нет задачи обновления"

    def terminate(self, proc: asyncio.subprocess.Process) -> None:
        proc.terminate()

    def stop_signals(self) -> tuple[int, ...]:
        return (signal.SIGINT, signal.SIGTERM)

    def elevate_argv(self, argv: list[str]) -> list[str] | None:
        return ["sudo", *argv]

    def elevate_noninteractive_argv(self, argv: list[str]) -> list[str] | None:
        return ["sudo", "-n", *argv]

    def package_install_argv(self, package: str) -> list[str] | None:
        for manager, argv in PACKAGE_INSTALL_ARGV:
            if shutil.which(manager) is not None:
                return [*argv, package]
        return None

    def ping_argv(self, ip: str) -> list[str]:
        return ["ping", "-c", "1", "-W", "1", ip]

    def node_unit(self, config_path: Path) -> NodeUnit:
        return NodeUnit(
            "systemd-user",
            Path.home() / ".config" / "systemd" / "user" / NODE_UNIT_NAME,
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", NODE_UNIT_NAME],
            ["loginctl", "enable-linger", getpass.getuser()],
        )
