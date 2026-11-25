from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sa_home_node.proto.wire import (
    NODE_SERVICE,
    Address,
    Envelope,
    ProtoError,
    ServiceUnavailableError,
    make_request,
)

log = logging.getLogger(__name__)

PEER_ERRORS = (ProtoError, ServiceUnavailableError, TimeoutError, ConnectionError, OSError)


async def request_peer(
    link, node_id: str, peer_id: str, timeout_s: float, msg_type: str, payload: dict | None = None
) -> Envelope:
    request = make_request(
        msg_type,
        payload,
        src=Address(node=node_id, service=NODE_SERVICE),
        dst=Address(node=peer_id, service=NODE_SERVICE),
        timeout_s=timeout_s,
    )
    return await asyncio.wait_for(link.forward(request), timeout_s)


class Periodic:
    task_name: str
    loop_error: str
    _poll_interval_s: float
    _sleep: Callable[[float], Awaitable[Any]]
    _task: asyncio.Task | None = None

    async def tick(self) -> None:
        raise NotImplementedError

    async def _launch(self) -> None:
        with contextlib.suppress(Exception):
            await self.tick()
        self._task = asyncio.create_task(self._run(), name=self.task_name)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            await self._sleep(self._poll_interval_s)
            try:
                await self.tick()
            except Exception:
                log.exception(self.loop_error)
