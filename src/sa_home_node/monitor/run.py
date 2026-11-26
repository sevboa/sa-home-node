from __future__ import annotations

import asyncio
import logging

from sa_home_node import __version__
from sa_home_node.db import Database
from sa_home_node.monitor.config import apply_node_kind
from sa_home_node.monitor.cron import CronScheduler
from sa_home_node.monitor.db import apply_schema
from sa_home_node.monitor.exposition import store_renderer
from sa_home_node.monitor.housekeeping import HousekeepingJob
from sa_home_node.monitor.jobbase import DedupQueue, JobContext, JobWorker
from sa_home_node.monitor.payloads import BroadcastDispatcher
from sa_home_node.monitor.scan import SensorScanJob
from sa_home_node.monitor.sensors.source import SensorSource
from sa_home_node.monitor.service import MonitorService
from sa_home_node.monitor.smartscan import SmartScanJob
from sa_home_node.monitor.store import MonitorStore
from sa_home_node.plugins import ServiceContext
from sa_home_node.promexport import MetricsEndpoint

log = logging.getLogger(__name__)


class OwnedMonitorService(MonitorService):
    def __init__(self, sensors, store, queue, providers=(), *, version: str) -> None:
        super().__init__(sensors, store, queue, providers, version=version)
        self.ready = asyncio.Event()

    async def run_command(self, action: str, args: dict) -> dict:
        await self.ready.wait()
        return await super().run_command(action, args)


def make_handler(ctx: ServiceContext) -> MonitorService:
    return OwnedMonitorService(
        apply_node_kind(ctx.sections["sensors"], ctx.node_kind),
        MonitorStore(Database(ctx.config.db_path)),
        DedupQueue(),
        [make(ctx) for plugin in ctx.plugins for make in plugin.sensor_providers],
        version=__version__,
    )


async def run_monitor(ctx: ServiceContext) -> None:
    handler = ctx.handler
    store, queue = handler.store, handler.queue
    sensors, metrics, schedule = handler.sensors, ctx.sections["metrics"], ctx.sections["schedule"]
    scheduler = CronScheduler()
    for name, cron, job_class in (
        ("sensor_scan", schedule.scan_cron, SensorScanJob),
        ("smart_scan", schedule.smart_cron, SmartScanJob),
        ("housekeeping", schedule.housekeeping_cron, HousekeepingJob),
    ):
        scheduler.add(name, cron, lambda job_class=job_class: queue.put(job_class()))
    await store.db.open()
    endpoint = None
    worker_task = None
    try:
        await apply_schema(store.db)
        handler.ready.set()
        job_ctx = JobContext(
            store=store,
            sensors=SensorSource(sensors, handler.providers),
            dispatcher=BroadcastDispatcher(ctx.broadcast),
            config=sensors,
            metrics=metrics,
        )
        worker_task = asyncio.create_task(JobWorker(queue, job_ctx).run(), name="job-worker")
        await scheduler.start()
        await queue.put(SmartScanJob())
        await queue.put(SensorScanJob())
        if metrics.export:
            endpoint = MetricsEndpoint(store_renderer(store), metrics.exporter_port)
            await endpoint.start()
        log.info("Монитор запущен")
        await asyncio.Event().wait()
    finally:
        log.info("Останов монитора...")
        if endpoint is not None:
            await endpoint.stop()
        await scheduler.stop()
        await queue.stop()
        if worker_task is not None:
            await worker_task
        await store.db.close()
        log.info("Монитор остановлен чисто")
