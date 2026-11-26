import asyncio
import logging
from typing import Any

from sa_home_node.db import Database
from sa_home_node.plugins import ServiceContext
from sa_home_node.tasks.config import TasksConfig
from sa_home_node.tasks.kinds import TIMER
from sa_home_node.tasks.link import NodeLinkClient
from sa_home_node.tasks.loops import TaskRunner
from sa_home_node.tasks.service import TasksService
from sa_home_node.tasks.store import TaskStore, apply_schema
from sa_home_node.wake.peers import MemoryWakeCache

log = logging.getLogger(__name__)


class OwnedTasksService(TasksService):
    def __init__(
        self, config: TasksConfig, db: Database, link: NodeLinkClient, runner: TaskRunner, node: str
    ) -> None:
        super().__init__(config, TaskStore(db), runner, node=node)
        self.db = db
        self.link = link
        self.runner = runner
        self.ready = asyncio.Event()

    async def run_command(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        await self.ready.wait()
        return await super().run_command(action, args)


def make_handler(ctx: ServiceContext) -> TasksService:
    config: TasksConfig = ctx.config

    async def emit(event_type: str, data: dict[str, Any]) -> None:
        if ctx.broadcast is not None:
            await ctx.broadcast(event_type, data)

    db = Database(config.db_path)
    link = NodeLinkClient(ctx.node_socket, ctx.swarm_token)
    kinds = [TIMER, *(kind for plugin in ctx.plugins for kind in plugin.task_kinds)]
    runner = TaskRunner(link, TaskStore(db), MemoryWakeCache(), kinds, emit, config)
    return OwnedTasksService(config, db, link, runner, ctx.node_id)


async def run_tasks(ctx: ServiceContext) -> None:
    service: OwnedTasksService = ctx.handler
    await service.db.open()
    await apply_schema(service.db)
    service.ready.set()
    service.runner.start()
    log.info("Служба tasks запущена")
    try:
        await asyncio.Event().wait()
    finally:
        await service.runner.stop()
        await service.link.close()
        await service.db.close()
        log.info("Служба tasks остановлена")
