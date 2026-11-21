import asyncio
import contextlib
import dataclasses
import logging
import signal
from pathlib import Path

from sa_home_node.config import load_config
from sa_home_node.content import ContentStore
from sa_home_node.host import current
from sa_home_node.host.base import install_stop_handlers
from sa_home_node.plugins import PluginError, ServiceFenced, load_plugins, make_context
from sa_home_node.proto.server import ProtoServer
from sa_home_node.services.events import FENCED_EXIT_CODE
from sa_home_node.services.inproc import InProcessService
from sa_home_node.services.registry import ServiceTable
from sa_home_node.swarm.node import Swarm, SwarmApi
from sa_home_node.swarm.state import NodeState

RESTART_DELAY_S = 5.0

log = logging.getLogger(__name__)


async def _noop(event_type: str, data: dict) -> None:
    return None


async def run_node(
    config_path: Path | None = None,
    stop: asyncio.Event | None = None,
    restart_delay_s: float = RESTART_DELAY_S,
) -> None:
    cfg = load_config(config_path)
    plugins = load_plugins(cfg.node.plugins)
    content = ContentStore(cfg.content_dir)
    swarm = Swarm(cfg, NodeState.load(cfg.node.state_path))
    for plugin in plugins:
        for action in plugin.node_actions:
            swarm.node_service.actions.add(action.spec, action.handler)
    api = SwarmApi(swarm)
    contexts = [(p, make_context(p, cfg, content, api)) for p in plugins]
    for _, ctx in contexts:
        ctx.data_dir.mkdir(parents=True, exist_ok=True)
    own_stop = stop is None
    if own_stop:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
    services = [
        InProcessService(s, ctx, emit=_noop, restart_delay_s=restart_delay_s)
        for p, ctx in contexts
        for s in p.services
    ]
    try:
        await swarm.start()
        for svc in services:
            await svc.start()
        await stop.wait()
    finally:
        if own_stop:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
        for svc in reversed(services):
            await svc.stop()
        await swarm.stop()


async def run_service(
    name: str,
    instance: str = "",
    config_path: Path | None = None,
    stop: asyncio.Event | None = None,
) -> int:
    cfg = load_config(config_path)
    plugins = load_plugins(cfg.node.plugins)
    content = ContentStore(cfg.content_dir)
    table = ServiceTable(plugins)
    spec = table.spec(name)
    if spec is None or not spec.process:
        raise PluginError(f"нет процессной службы: {name}")
    ctx = dataclasses.replace(make_context(table.plugin_of(name), cfg, content), instance=instance)
    ctx.data_dir.mkdir(parents=True, exist_ok=True)
    own_stop = stop is None
    if own_stop:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        remove = install_stop_handlers(loop, current().stop_signals(), stop.set)
    server = None
    if spec.handler is not None and spec.endpoint is not None:
        server = ProtoServer(spec.endpoint(ctx.config), spec.handler(ctx))
    pending: list[asyncio.Task] = []
    try:
        if server is not None:
            await server.start()
        run_task = asyncio.create_task(spec.run(ctx))
        stop_task = asyncio.create_task(stop.wait())
        try:
            done, pending = await asyncio.wait(
                {run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                exc = task.exception()
                if exc is not None:
                    if isinstance(exc, ServiceFenced):
                        return FENCED_EXIT_CODE
                    raise exc
            return 0
        finally:
            for task in pending:
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*pending)
    finally:
        if server is not None:
            await server.stop()
        if own_stop:
            remove()
