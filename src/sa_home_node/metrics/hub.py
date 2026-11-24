from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sa_home_node import promexport
from sa_home_node.metrics.runtime import runtime_files
from sa_home_node.metrics.writeout import write_changed
from sa_home_node.services.periodic import Periodic

log = logging.getLogger(__name__)

INTERVAL_S = 60.0


class MetricsHub(Periodic):
    task_name = "metrics-hub"
    loop_error = "метрики: ошибка в цикле обновления"

    def __init__(
        self,
        ctx: Any,
        *,
        address: Callable[[], str | None] = promexport.overlay_address,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.ctx = ctx
        self._address = address
        self._poll_interval_s = INTERVAL_S
        self._sleep = sleep

    async def start(self) -> None:
        await self._launch()

    async def tick(self) -> None:
        loop = asyncio.get_running_loop()
        own_host = await loop.run_in_executor(None, self._address)
        files = runtime_files(
            self.ctx.config, self.ctx.node_id, own_host, self.ctx.swarm.peers(), self.ctx.plugins
        )
        try:
            written = await loop.run_in_executor(None, write_changed, files)
        except OSError:
            log.warning("метрики: ошибка записи", exc_info=True)
            return

        if written:
            names = ", ".join(str(p.relative_to(self.ctx.config.runtime_dir)) for p in written)
            log.info("Метрики: обновлены файлы %s", names)


async def run_hub(ctx: Any) -> None:
    hub = MetricsHub(ctx)
    await hub.start()
    try:
        await asyncio.Event().wait()
    finally:
        await hub.stop()
