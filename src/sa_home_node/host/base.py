from __future__ import annotations

import asyncio
import signal
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

TCP_KEEPALIVE_IDLE_S = 20
TCP_KEEPALIVE_INTERVAL_S = 10
TCP_KEEPALIVE_COUNT = 3
TCP_USER_TIMEOUT_S = 30
WINDOWS_TCP_MAXRT = 5
RESTART_EXIT_CODE = 10
POWER_ACTIONS = ("poweroff", "reboot", "suspend")


@dataclass(frozen=True)
class NodeUnit:
    format: str
    path: Path
    reload_argv: list[str] | None


def check_power_action(action: str) -> None:
    if action not in POWER_ACTIONS:
        raise ValueError(action)


class Host(Protocol):
    name: str
    unix_sockets: bool
    update_via_task: bool

    def enable_tcp_keepalive(self, sock: socket.socket) -> None:
        ...

    def system_uptime_s(self) -> float | None:
        ...

    def power_argv(self, action: str) -> list[str]: ...

    def self_restart(self, argv: list[str]) -> int: ...

    async def trigger_update_task(self) -> tuple[bool, str]: ...

    def spawn_kwargs(self) -> dict: ...

    def terminate(self, proc: asyncio.subprocess.Process) -> None: ...

    def stop_signals(self) -> tuple[int, ...]: ...

    def elevate_argv(self, argv: list[str]) -> list[str] | None: ...

    def package_install_argv(self, package: str) -> list[str] | None: ...

    def node_unit(self, config_path: Path) -> NodeUnit: ...


def install_stop_handlers(
    loop: asyncio.AbstractEventLoop, signals: tuple[int, ...], callback: Callable[[], None]
) -> Callable[[], None]:
    handled: list[int] = []
    restored: list[tuple[int, Any]] = []
    for sig in signals:
        try:
            loop.add_signal_handler(sig, callback)
            handled.append(sig)
        except NotImplementedError:
            restored.append((sig, signal.signal(sig, lambda *_: callback())))

    def remove() -> None:
        for sig in handled:
            loop.remove_signal_handler(sig)
        for sig, previous in restored:
            signal.signal(sig, previous)

    return remove
