import asyncio
import logging
import signal
from pathlib import Path

from sa_home_node.config import load_config
from sa_home_node.content import ContentStore
from sa_home_node.plugins import ServiceContext, ServiceSpec, load_plugins, make_context

RESTART_DELAY_S = 5.0

log = logging.getLogger(__name__)


async def supervise(name: str, spec: ServiceSpec, ctx: ServiceContext, restart_delay_s: float) -> None:
    while True:
        try:
            await spec.run(ctx)
        except Exception:
            log.exception("Служба %s упала, перезапуск через %s с", name, restart_delay_s)
            await asyncio.sleep(restart_delay_s)
        else:
            log.info("Служба %s завершилась", name)
            return


async def run_node(
    config_path: Path | None = None,
    stop: asyncio.Event | None = None,
    restart_delay_s: float = RESTART_DELAY_S,
) -> None:
    cfg = load_config(config_path)
    plugins = load_plugins(cfg.node.plugins)
    content = ContentStore(cfg.content_dir)
    contexts = [(p, make_context(p, cfg, content)) for p in plugins]
    for _, ctx in contexts:
        ctx.data_dir.mkdir(parents=True, exist_ok=True)
    own_stop = stop is None
    if own_stop:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
    tasks = [
        asyncio.create_task(supervise(f"{p.name}.{s.name}", s, ctx, restart_delay_s))
        for p, ctx in contexts
        for s in p.services
    ]
    try:
        await stop.wait()
    finally:
        if own_stop:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
        for task in reversed(tasks):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
