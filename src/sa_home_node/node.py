import asyncio
import logging
import signal
from pathlib import Path

from sa_home_node.config import load_config
from sa_home_node.content import ContentStore
from sa_home_node.plugins import load_plugins, make_context
from sa_home_node.services.inproc import InProcessService
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
