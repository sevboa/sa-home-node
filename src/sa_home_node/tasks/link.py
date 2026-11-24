import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.wire import Address


class NodeLinkClient:
    def __init__(self, endpoint: str | Path, token: str = "") -> None:
        self._endpoint = endpoint
        self._token = token
        self._client: ProtoClient | None = None
        self._lock = asyncio.Lock()

    async def get_state(self, dst: Address | None = None) -> dict[str, Any]:
        return await self._call(lambda client: client.get_state(dst=dst))

    async def command(
        self,
        action: str,
        args: dict[str, Any] | None = None,
        dst: Address | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return await self._call(lambda client: client.command(action, args, dst=dst, timeout=timeout))

    async def close(self) -> None:
        async with self._lock:
            await self._drop()

    async def _connected(self) -> ProtoClient:
        async with self._lock:
            if self._client is None or not self._client.connected:
                await self._drop()
                client = ProtoClient(self._endpoint, token=self._token, src=Address(service="tasks"))
                await client.connect()
                self._client = client
            return self._client

    async def _drop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()

    async def _call(self, send: Callable[[ProtoClient], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        for attempt in (1, 2):
            try:
                return await send(await self._connected())
            except TimeoutError:
                raise
            except OSError as exc:
                await self.close()
                if attempt == 2:
                    raise ConnectionError(str(exc)) from exc
        raise AssertionError
