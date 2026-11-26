from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sa_home_node.proto.client import EventCallback, ProtoClient
from sa_home_node.proto.describe import ActionSpec, ServiceDescription
from sa_home_node.proto.endpoints import parse_endpoint
from sa_home_node.proto.wire import Address, ProtoError, ServiceUnavailableError

log = logging.getLogger(__name__)

RECONNECT_DELAY_S = 5.0
_LINK_ERRORS = (ConnectionError, OSError, TimeoutError, ProtoError)


class ServiceTimeoutError(ServiceUnavailableError):
    pass


class ServiceLink:
    def __init__(
        self,
        endpoint: str | Path,
        *,
        token: str = "",
        display_name: str = "служба",
        on_event: EventCallback | None = None,
        on_connected: Callable[[], Awaitable[None]] | None = None,
        reconnect_delay: float = RECONNECT_DELAY_S,
    ) -> None:
        self._endpoint = parse_endpoint(endpoint)
        self._token = token
        self.display_name = display_name
        self._on_event = on_event
        self._on_connected = on_connected
        self._delay = reconnect_delay
        self._client: ProtoClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._description: ServiceDescription | None = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"service-link-{self.display_name}")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def get_state(self, dst: Address | None = None) -> dict[str, Any]:
        client = self._require_client()
        try:
            return await client.get_state(dst=dst)
        except (ConnectionError, OSError, TimeoutError) as exc:
            raise ServiceUnavailableError(str(exc)) from exc

    async def command(
        self,
        action: str,
        args: dict[str, Any] | None = None,
        dst: Address | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        client = self._require_client()
        try:
            return await client.command(action, args, dst=dst, timeout=timeout)
        except TimeoutError as exc:
            raise ServiceTimeoutError(str(exc) or "нет ответа") from exc
        except (ConnectionError, OSError) as exc:
            raise ServiceUnavailableError(str(exc)) from exc

    async def describe(self, dst: Address | None = None) -> ServiceDescription | None:
        client = self._client
        if client is None:
            return None
        try:
            return await client.describe(dst=dst)
        except _LINK_ERRORS:
            return None

    async def actions(self) -> tuple[ActionSpec, ...]:
        fresh = await self.describe()
        if fresh is not None:
            self._description = fresh
        return self._description.actions if self._description is not None else ()

    def _require_client(self) -> ProtoClient:
        client = self._client
        if client is None:
            raise ServiceUnavailableError(f"нет соединения: {self.display_name} ({self._endpoint})")
        return client

    async def _run(self) -> None:
        logged_down = False
        while True:
            client = ProtoClient(
                self._endpoint,
                token=self._token,
                src=Address(service="telegram-bot"),
                on_event=self._on_event,
            )
            try:
                await client.connect()
                info = await client.hello()
                with contextlib.suppress(ProtoError):
                    self._description = await client.describe()
                log.info("Связь со службой установлена: %s/%s v%s", info.node, info.service, info.version)
                logged_down = False
                self._client = client
                await self._connected_hook()
                await client.join()
                log.warning("Связь со службой %s потеряна, переподключение...", self.display_name)
            except _LINK_ERRORS as exc:
                if logged_down:
                    log.debug("Служба %s всё ещё недоступна: %s", self.display_name, exc)
                else:
                    log.warning(
                        "Служба %s недоступна (%s) — переподключение каждые %.0f с",
                        self.display_name,
                        exc,
                        self._delay,
                    )
                    logged_down = True
            finally:
                self._client = None
                await client.close()
            await asyncio.sleep(self._delay)

    async def _connected_hook(self) -> None:
        if self._on_connected is None:
            return
        try:
            await self._on_connected()
        except Exception:
            log.warning("Ошибка on_connected-хука службы %s", self.display_name, exc_info=True)
