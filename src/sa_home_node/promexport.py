from __future__ import annotations

import asyncio
import logging
import re
import socket
from collections.abc import Awaitable, Callable, Iterable

import psutil

from sa_home_node.netif import is_tun

log = logging.getLogger(__name__)

METRICS_PATH = "/metrics"
CONTENT_TYPE = "text/plain; version=0.0.4"
METRIC_UP = "sa_up"
HOST_METRIC_PREFIX = "sa_host_"
METRIC_TEMPERATURE = "sa_temperature_celsius"
LABEL_KIND = "kind"
LABEL_COMPONENT = "component"

BIND_RETRY_S = 15.0
METRIC_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

Render = Callable[[], Awaitable[str]]


def _label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    body = ",".join(f'{key}="{_label_value(value)}"' for key, value in labels.items())
    return "{" + body + "}"


def sample(name: str, labels: dict[str, str], value: float) -> str:
    return f"{name}{format_labels(labels)} {value:g}"


def gauge_family(name: str, title: str, samples: Iterable[str]) -> list[str]:
    return [f"# HELP {name} {title}", f"# TYPE {name} gauge", *samples]


def overlay_address() -> str | None:
    for iface, addrs in psutil.net_if_addrs().items():
        if not is_tun(iface):
            continue
        for addr in addrs:
            if addr.family == socket.AF_INET and addr.address:
                return addr.address
    return None


def _response(status: str, body: bytes, content_type: str) -> bytes:
    head = (
        f"HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode() + body


class MetricsEndpoint:
    def __init__(
        self,
        render: Render,
        port: int,
        address: Callable[[], str | None] = overlay_address,
        retry_s: float = BIND_RETRY_S,
    ) -> None:
        self._render = render
        self._port = port
        self._address = address
        self._retry_s = retry_s
        self._server: asyncio.Server | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._bind_when_ready(), name="metrics-endpoint")

    async def _bind_when_ready(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            host = await loop.run_in_executor(None, self._address)
            if host:
                try:
                    self._server = await asyncio.start_server(self._handle, host, self._port)
                except OSError as exc:
                    log.warning("метрики: не удалось слушать %s:%d: %s", host, self._port, exc)
                else:
                    log.info("Метрики: http://%s:%d%s", host, self._port, METRICS_PATH)
                    return
            else:
                log.warning("метрики: нет адреса оверлейной сети, повтор через %.0f с", self._retry_s)
            await asyncio.sleep(self._retry_s)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            parts = head.split(b"\r\n", 1)[0].split()
            if len(parts) >= 2 and parts[0] == b"GET" and parts[1].split(b"?")[0] == METRICS_PATH.encode():
                reply = _response("200 OK", (await self._render()).encode(), CONTENT_TYPE)
            else:
                reply = _response("404 Not Found", b"404: Not Found", "text/plain; charset=utf-8")
            writer.write(reply)
            await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            pass
        finally:
            writer.close()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
