from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sa_home_node.services.assignments import Assignment
from sa_home_node.services.events import (
    EVENT_SERVICE_FENCED,
    EVENT_SERVICE_STOPPED,
    STOPPED,
    EventEmitter,
    call_quietly,
    emit_quietly,
    slot_dict,
)

log = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


class SlotBase:
    pid: int | None = None

    def __init__(
        self,
        name: str,
        assignment: Assignment,
        *,
        emit: EventEmitter,
        restart_delay_s: float,
        stop_timeout_s: float,
        on_fenced: Callable[[str], None] | None,
        on_spawned: Callable[[str], None] | None,
    ) -> None:
        self.name = name
        self.assignment = assignment
        self._emit = emit
        self._restart_delay = restart_delay_s
        self._stop_timeout = stop_timeout_s
        self._on_fenced = on_fenced
        self._on_spawned = on_spawned
        self._task: asyncio.Task | None = None
        self._desired_running = False
        self._status = STOPPED
        self.restarts = 0
        self.last_exit_code: int | None = None
        self.started_at: str | None = None

    @property
    def status(self) -> str:
        return self._status

    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError

    def to_dict(self) -> dict:
        return slot_dict(self)

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _event(self, event: str, **payload) -> None:
        await emit_quietly(self._emit, event, {"name": self.name, **payload})

    async def _stopped(self) -> None:
        self._status = STOPPED
        await self._event(EVENT_SERVICE_STOPPED)

    async def _fenced(self) -> None:
        self._desired_running = False
        self._status = STOPPED
        await self._event(EVENT_SERVICE_FENCED)
        call_quietly(self._on_fenced, self.name)

    async def _cancel(self, task: asyncio.Task, timeout: float, what: str) -> None:
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            log.warning("Служба %s: %s за %.0f с — снимаю", self.name, what, timeout)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
