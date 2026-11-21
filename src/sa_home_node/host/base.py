from __future__ import annotations

import asyncio
import signal
import socket
from collections.abc import Callable
from typing import Protocol

TCP_KEEPALIVE_IDLE_S = 20
TCP_KEEPALIVE_INTERVAL_S = 10
TCP_KEEPALIVE_COUNT = 3
TCP_USER_TIMEOUT_S = 30
WINDOWS_TCP_MAXRT = 5
RESTART_EXIT_CODE = 10
POWER_ACTIONS = ("poweroff", "reboot", "suspend")


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

    def power_argv(self, action: str) -> list[str]:
        ...

    def self_restart(self, argv: list[str]) -> int:
        ...

    async def trigger_update_task(self) -> tuple[bool, str]:
        ...

    def spawn_kwargs(self) -> dict:
        ...

    def terminate(self, proc: asyncio.subprocess.Process) -> None:
        ...

    def stop_signals(self) -> tuple[int, ...]:
        ...


def install_stop_handlers(
    loop: asyncio.AbstractEventLoop,
    signals: tuple[int, ...],
    callback: Callable[[], None],
) -> Callable[[], None]:
    handled: list[int] = []
    restored: list[int] = []
    previous: list = []
    for sig in signals:
        try:
            loop.add_signal_handler(sig, callback)
            handled.append(sig)
        except NotImplementedError:
            previous.append(signal.signal(sig, lambda *_: callback()))
            restored.append(sig)

    def remove() -> None:
        for sig in handled:
            loop.remove_signal_handler(sig)
        for i, sig in enumerate(restored):
            signal.signal(sig, previous[i])

    return remove
