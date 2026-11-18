from __future__ import annotations

import contextlib
import socket

from .base import (
    TCP_KEEPALIVE_IDLE_S,
    TCP_KEEPALIVE_INTERVAL_S,
    TCP_USER_TIMEOUT_S,
    WINDOWS_TCP_MAXRT,
)


class WindowsHost:
    name = "windows"
    unix_sockets = False

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
