from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.services.slot import utc_now

if TYPE_CHECKING:
    from sa_home_node.monitor.config import SensorsConfig
    from sa_home_node.monitor.hostmodels import HostEvent
    from sa_home_node.monitor.models import DispatchResult, Event, SmartChange
    from sa_home_node.monitor.sensors.source import SensorSource
    from sa_home_node.monitor.store import MonitorStore

log = logging.getLogger(__name__)


class EventDispatcher(Protocol):
    async def dispatch_alert(self, event: Event) -> DispatchResult: ...

    async def dispatch_clear(self, event: Event) -> DispatchResult: ...

    async def dispatch_smart(self, change: SmartChange) -> DispatchResult: ...

    async def dispatch_host_alert(self, event: HostEvent) -> DispatchResult: ...

    async def dispatch_host_clear(self, event: HostEvent) -> DispatchResult: ...


@dataclass
class JobContext:
    store: MonitorStore
    sensors: SensorSource
    dispatcher: EventDispatcher
    config: SensorsConfig
    metrics: MetricsConfig = field(default_factory=MetricsConfig)


@dataclass
class JobResult:
    components_scanned: int = 0
    transitions: int = 0
    alerts_sent: int = 0
    clears_sent: int = 0
    extra: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class SensorJob(Protocol):
    @property
    def dedup_key(self) -> str: ...

    @property
    def job_type(self) -> str: ...

    async def run(self, ctx: JobContext) -> JobResult: ...


@runtime_checkable
class Dedupable(Protocol):
    @property
    def dedup_key(self) -> str: ...


class DedupQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._keys: set[str] = set()

    async def put(self, job: Dedupable) -> bool:
        if job.dedup_key in self._keys:
            return False
        self._keys.add(job.dedup_key)
        await self._queue.put(job)
        return True

    async def get(self):
        job = await self._queue.get()
        if job is not None:
            self._keys.discard(job.dedup_key)
        return job

    def task_done(self) -> None:
        self._queue.task_done()

    async def stop(self) -> None:
        await self._queue.put(None)

    def qsize(self) -> int:
        return self._queue.qsize()


class JobWorker:
    def __init__(self, queue: DedupQueue, ctx: JobContext) -> None:
        self._queue = queue
        self._ctx = ctx

    async def run(self) -> None:
        log.info("JobWorker запущен")
        while True:
            job = await self._queue.get()
            if job is None:
                self._queue.task_done()
                break
            await self._execute(job)
            self._queue.task_done()
        log.info("JobWorker остановлен")

    async def _execute(self, job) -> None:
        run_id = await self._ctx.store.start_job_run(job.job_type, utc_now())
        try:
            result = await job.run(self._ctx)
            await self._ctx.store.finish_job_run(
                run_id, "ok", utc_now(), metrics_json=result.to_json()
            )
            log.info("Job %s завершён: %s", job.job_type, result.to_json())
        except Exception as exc:
            log.exception("Job %s упал", job.job_type)
            await self._ctx.store.finish_job_run(run_id, "error", utc_now(), error=str(exc))
