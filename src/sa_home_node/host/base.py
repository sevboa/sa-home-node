from __future__ import annotations

import socket
from typing import Protocol

TCP_KEEPALIVE_IDLE_S = 20
TCP_KEEPALIVE_INTERVAL_S = 10
TCP_KEEPALIVE_COUNT = 3
TCP_USER_TIMEOUT_S = 30
WINDOWS_TCP_MAXRT = 5


class Host(Protocol):
    name: str
    unix_sockets: bool

    def enable_tcp_keepalive(self, sock: socket.socket) -> None:
        ...

    def system_uptime_s(self) -> float | None:
        ...
