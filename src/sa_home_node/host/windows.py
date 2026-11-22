from __future__ import annotations

import asyncio
import contextlib
import signal
import socket
import subprocess
from pathlib import Path

from .base import (
    RESTART_EXIT_CODE,
    TCP_KEEPALIVE_IDLE_S,
    TCP_KEEPALIVE_INTERVAL_S,
    TCP_USER_TIMEOUT_S,
    WINDOWS_TCP_MAXRT,
    NodeUnit,
    check_power_action,
)

WINDOWS_AUTOUPDATE_TASK_NAME = "sa-home-node-autoupdate"
SCHTASKS_RUN_TIMEOUT_S = 30
WINDOWS_POWER_ARGV = {
    "poweroff": ["shutdown", "/s", "/t", "5"],
    "reboot": ["shutdown", "/r", "/t", "5"],
    "suspend": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
}


class WindowsHost:
    name = "windows"
    unix_sockets = False
    update_via_task = True

    def enable_tcp_keepalive(self, sock: socket.socket) -> None:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sio_keepalive_vals = getattr(socket, "SIO_KEEPALIVE_VALS", None)
        if sio_keepalive_vals is not None:
            with contextlib.suppress(OSError, AttributeError):
                sock.ioctl(
                    sio_keepalive_vals,
                    (1, TCP_KEEPALIVE_IDLE_S * 1000, TCP_KEEPALIVE_INTERVAL_S * 1000),
                )
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.IPPROTO_TCP, WINDOWS_TCP_MAXRT, TCP_USER_TIMEOUT_S)

    def system_uptime_s(self) -> float | None:
        import ctypes

        return ctypes.windll.kernel32.GetTickCount64() / 1000.0

    def power_argv(self, action: str) -> list[str]:
        check_power_action(action)
        return list(WINDOWS_POWER_ARGV[action])

    def self_restart(self, argv: list[str]) -> int:
        return RESTART_EXIT_CODE

    async def trigger_update_task(self) -> tuple[bool, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "schtasks",
                "/run",
                "/tn",
                WINDOWS_AUTOUPDATE_TASK_NAME,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=SCHTASKS_RUN_TIMEOUT_S)
        except (OSError, TimeoutError) as exc:
            return False, str(exc)
        return proc.returncode == 0, stdout.decode(errors="replace").strip()

    def spawn_kwargs(self) -> dict:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}

    def terminate(self, proc: asyncio.subprocess.Process) -> None:
        proc.send_signal(getattr(signal, "CTRL_BREAK_EVENT", 1))

    def stop_signals(self) -> tuple[int, ...]:
        sigbreak = getattr(signal, "SIGBREAK", None)
        return (signal.SIGINT, signal.SIGTERM) + (() if sigbreak is None else (sigbreak,))

    def elevate_argv(self, argv: list[str]) -> list[str] | None:
        return None

    def package_install_argv(self, package: str) -> list[str] | None:
        return None

    def node_unit(self, config_path: Path) -> NodeUnit:
        return NodeUnit("winsw", Path(config_path).parent / "service" / "sa-home-node.xml", None)
