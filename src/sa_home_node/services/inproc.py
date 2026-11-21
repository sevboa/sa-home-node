from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from sa_home_node.plugins import (
    ServiceContext,
    ServiceFenced,
    ServiceSpec,
)
from sa_home_node.proto.server import ServiceHandler, dispatch_request
from sa_home_node.proto.wire import Envelope
from sa_home_node.services.assignments import Assignment
from sa_home_node.services.events import (
    EVENT_SERVICE_FAILED,
    EVENT_SERVICE_FENCED,
    EVENT_SERVICE_STARTED,
    EVENT_SERVICE_STOPPED,
    FENCED_EXIT_CODE,
    RESTARTING,
    RUNNING,
    STOPPED,
    EventEmitter,
    slot_dict,
)
from sa_home_node.services.process import utc_now

log = logging.getLogger(__name__)


class InProcessService:
    def __init__(
        self,
        spec: ServiceSpec,
        ctx: ServiceContext,
        *,
        emit: EventEmitter,
        restart_delay_s: float = 5.0,
        stop_timeout_s: float = 90.0,
        assignment: Assignment | None = None,
        on_fenced: Callable[[str], None] | None = None,
    ) -> None:
        self.assignment = assignment or Assignment(service=spec.name)
        self.name = self.assignment.key
        self._spec = spec
        self._ctx = ctx
        self._emit = emit
        self._restart_delay = restart_delay_s
        self._stop_timeout = stop_timeout_s
        self._on_fenced = on_fenced
        self._task: asyncio.Task | None = None
        self._desired_running = False
        self._status = STOPPED
        self.restarts = 0
        self.last_exit_code: int | None = None
        self.started_at: str | None = None

    @property
    def status(self) -> str:
        return self._status

    @property
    def ctx(self) -> ServiceContext:
        return self._ctx

    @property
    def pid(self) -> int | None:
        return None

    @property
    def process_alive(self) -> bool:
        task = self._task
        return task is not None and not task.done() and self._status == RUNNING

    def to_dict(self) -> dict:
        return slot_dict(self)

    async def start(self) -> None:
        if self._desired_running:
            return
        self._desired_running = True
        self._task = asyncio.create_task(self._run(), name=f"inproc-{self.name}")

    async def stop(self) -> None:
        if not self._desired_running:
            return
        self._desired_running = False
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=self._stop_timeout)
        if not done:
            log.warning(
                "Служба %s: встроенная задача не остановилась за %.0f с — снимаю",
                self.name,
                self._stop_timeout,
            )
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._status = STOPPED
        with contextlib.suppress(Exception):
            await self._emit(EVENT_SERVICE_STOPPED, {"name": self.name})

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _run(self) -> None:
        while self._desired_running:
            try:
                self.started_at = utc_now().isoformat()
                self._status = RUNNING
                with contextlib.suppress(Exception):
                    await self._emit(EVENT_SERVICE_STARTED, {"name": self.name, "pid": None})
                await self._spec.run(self._ctx)
            except ServiceFenced:
                self.last_exit_code = FENCED_EXIT_CODE
                self._status = STOPPED
                self._desired_running = False
                with contextlib.suppress(Exception):
                    await self._emit(EVENT_SERVICE_FENCED, {"name": self.name})
                if self._on_fenced is not None:
                    self._on_fenced(self.name)
                break
            except Exception as exc:
                log.exception(
                    "Служба %s упала, перезапуск через %.0f с",
                    self.name,
                    self._restart_delay,
                )
                self.restarts += 1
                self._status = RESTARTING
                with contextlib.suppress(Exception):
                    await self._emit(EVENT_SERVICE_FAILED, {"name": self.name, "error": str(exc)})
                await asyncio.sleep(self._restart_delay)
            else:
                self._status = STOPPED
                self._desired_running = False
                with contextlib.suppress(Exception):
                    await self._emit(EVENT_SERVICE_STOPPED, {"name": self.name})
                break


class InProcessTarget:
    def __init__(self, service: InProcessService, handler: ServiceHandler) -> None:
        self._service = service
        self._handler = handler
        self.name = service.name

    @property
    def alive(self) -> bool:
        return self._service.process_alive

    @property
    def busy(self) -> bool:
        return bool(self._handler.describe().info.busy)

    @property
    def working(self) -> bool | None:
        return self._handler.describe().info.working

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def forward(self, env: Envelope) -> Envelope:
        return await dispatch_request(self._handler, env)
