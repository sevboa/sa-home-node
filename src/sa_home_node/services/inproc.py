from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from sa_home_node.plugins import ServiceContext, ServiceFenced, ServiceSpec
from sa_home_node.proto.server import ServiceHandler, dispatch_request
from sa_home_node.proto.wire import Envelope
from sa_home_node.services.assignments import Assignment
from sa_home_node.services.events import (
    EVENT_SERVICE_FAILED,
    EVENT_SERVICE_STARTED,
    FENCED_EXIT_CODE,
    RESTARTING,
    RUNNING,
    STOPPED,
    EventEmitter,
    call_quietly,
)
from sa_home_node.services.slot import SlotBase, utc_now

log = logging.getLogger(__name__)


class InProcessService(SlotBase):
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
        on_spawned: Callable[[str], None] | None = None,
    ) -> None:
        assignment = assignment or Assignment(service=spec.name)
        super().__init__(
            assignment.key, assignment, emit=emit, restart_delay_s=restart_delay_s,
            stop_timeout_s=stop_timeout_s, on_fenced=on_fenced, on_spawned=on_spawned,
        )
        self._spec = spec
        self.ctx = ctx

    @property
    def process_alive(self) -> bool:
        task = self._task
        return task is not None and not task.done() and self._status == RUNNING

    async def start(self) -> None:
        if self._desired_running:
            return
        self._desired_running = True
        self._task = asyncio.create_task(self._run(), name=f"inproc-{self.name}")

    async def stop(self) -> None:
        task = self._task
        if not self._desired_running and (task is None or task.done()):
            return
        self._desired_running = False
        if task is not None:
            task.cancel()
            try:
                await self._cancel(task, self._stop_timeout, "встроенная задача не остановилась")
            except BaseException:
                self._status = STOPPED
                task.cancel()
                raise
        self._task = None
        await self._stopped()

    async def _run(self) -> None:
        while self._desired_running:
            try:
                self.started_at = utc_now().isoformat()
                self._status = RUNNING
                call_quietly(self._on_spawned, self.name)
                await self._event(EVENT_SERVICE_STARTED, pid=None)
                await self._spec.run(self.ctx)
            except ServiceFenced:
                self.last_exit_code = FENCED_EXIT_CODE
                await self._fenced()
                break
            except Exception as exc:
                log.exception("Служба %s упала, перезапуск через %.0f с", self.name, self._restart_delay)
                self.restarts += 1
                self._status = RESTARTING
                await self._event(EVENT_SERVICE_FAILED, error=str(exc))
                await asyncio.sleep(self._restart_delay)
            else:
                self._desired_running = False
                await self._stopped()
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
