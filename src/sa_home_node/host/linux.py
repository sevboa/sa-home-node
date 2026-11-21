from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket

from .base import (
    TCP_KEEPALIVE_COUNT,
    TCP_KEEPALIVE_IDLE_S,
    TCP_KEEPALIVE_INTERVAL_S,
    TCP_USER_TIMEOUT_S,
    check_power_action,
)


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
                sock.setsockopt(
                    socket.IPPROTO_TCP, user_timeout_opt, TCP_USER_TIMEOUT_S * 1000
                )
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
