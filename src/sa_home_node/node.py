import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import Callable
from pathlib import Path

from sa_home_node.config import NodeConfig, load_config
from sa_home_node.content import ContentStore
from sa_home_node.host import current
from sa_home_node.host.base import install_stop_handlers
from sa_home_node.lifecycle.power import register_power
from sa_home_node.lifecycle.shutdown import STEP_TIMEOUT_S, ShutdownBudget, arm_hard_exit
from sa_home_node.lifecycle.update import origin_repo_url, register_update
from sa_home_node.plugins import PluginError, ServiceContext, ServiceFenced, load_plugins, make_context
from sa_home_node.proto.server import ProtoServer
from sa_home_node.services.actions import local_target, register_supervisor
from sa_home_node.services.assignments import effective, parse_valid
from sa_home_node.services.events import FENCED_EXIT_CODE, EventEmitter
from sa_home_node.services.inproc import InProcessService
from sa_home_node.services.instances import INSTANCES_DIRNAME, InstanceStore
from sa_home_node.services.lease import LeaseManager
from sa_home_node.services.process import ProcessService
from sa_home_node.services.registry import ServiceTable
from sa_home_node.services.replication import ConfigReplicator
from sa_home_node.services.slot import SlotBase
from sa_home_node.services.supervisor import SlotFactory, Supervisor, process_argv
from sa_home_node.swarm.config import swarm_section
from sa_home_node.swarm.node import Swarm, SwarmApi
from sa_home_node.swarm.state import NodeState

SHUTDOWN_BUDGET_S = 20.0
HARD_EXIT_MARGIN_S = 15.0

log = logging.getLogger(__name__)


def instance_store(config: NodeConfig) -> InstanceStore:
    return InstanceStore(config.path.parent / INSTANCES_DIRNAME, config.node.id)


def with_instance(ctx: ServiceContext, store: InstanceStore, service: str, instance: str) -> ServiceContext:
    package = store.package_path(service, instance) if instance else None
    return dataclasses.replace(ctx, instance=instance, package_path=package)


def make_slot_factory(
    config: NodeConfig,
    table: ServiceTable,
    contexts: dict,
    store: InstanceStore,
    *,
    emit: EventEmitter,
    restart_delay_s: float,
    on_fenced: Callable[[str], None],
    on_spawned: Callable[[str], None],
) -> SlotFactory:
    def factory(spec, assignment) -> SlotBase:
        common = {
            "emit": emit,
            "restart_delay_s": restart_delay_s,
            "stop_timeout_s": config.node.stop_timeout_s,
            "assignment": assignment,
            "on_fenced": on_fenced,
            "on_spawned": on_spawned,
        }
        if spec.process:
            argv = process_argv(spec.name, assignment.instance, str(config.path))
            return ProcessService(assignment.key, argv, **common)
        base = contexts[table.plugin_of(spec.name).name]
        return InProcessService(spec, with_instance(base, store, spec.name, assignment.instance), **common)

    return factory


async def run_node(
    config_path: Path | None = None, stop: asyncio.Event | None = None, restart_delay_s: float | None = None
) -> bool:
    cfg = load_config(config_path)
    plugins = load_plugins(cfg.node.plugins)
    content = ContentStore(cfg.content_dir)
    table = ServiceTable(plugins)
    state = NodeState.load(cfg.node.state_path)
    swarm = Swarm(cfg, state)
    node_service = swarm.node_service
    router = swarm.router
    for plugin in plugins:
        for action in plugin.node_actions:
            node_service.actions.add(action.spec, action.handler)
    api = SwarmApi(swarm)
    contexts = {p.name: make_context(p, cfg, content, api, plugins) for p in plugins}
    for ctx in contexts.values():
        ctx.data_dir.mkdir(parents=True, exist_ok=True)

    restart_requested = False
    stop_event = asyncio.Event() if stop is None else stop

    def request_restart() -> None:
        nonlocal restart_requested
        restart_requested = True
        log.warning("Нода %s: запрошен самоперезапуск", cfg.node.id)
        stop_event.set()

    remove_handlers = None
    if stop is None:
        remove_handlers = install_stop_handlers(
            asyncio.get_running_loop(), current().stop_signals(), stop_event.set
        )

    lease: LeaseManager | None = None

    def on_fenced(slot_name: str) -> None:
        if lease is not None:
            lease.note_fenced(slot_name)

    def on_spawned(slot_name: str) -> None:
        if lease is not None:
            lease.set_ready(slot_name, False)

    store = instance_store(cfg)
    assignments = effective(cfg.node.assignments, state.assignments)
    delay_s = cfg.node.restart_delay_s if restart_delay_s is None else restart_delay_s
    factory = make_slot_factory(
        cfg, table, contexts, store, emit=swarm.emit, restart_delay_s=delay_s, on_fenced=on_fenced, on_spawned=on_spawned
    )
    supervisor = Supervisor(assignments, table, factory)
    if not supervisor.services:
        log.warning("Нет ни одного валидного назначения — нода работает вхолостую")
    tuning = swarm.swarm
    replicator = ConfigReplicator(
        cfg.node.id, store, supervisor, table, router=router, emit=swarm.emit, subscribe=swarm.subscribe,
        hooks=[p.replication_hooks for p in plugins if p.replication_hooks is not None],
        poll_interval_s=tuning.replication_poll_interval_s,
    )
    lease = LeaseManager(
        cfg.node.id, cfg.node.kind, supervisor, table, router=router, emit=swarm.emit,
        grace_s=tuning.failover_grace_s, ready_timeout_s=tuning.superior_ready_timeout_s,
        poll_interval_s=tuning.lease_poll_interval_s, settle_s=tuning.lease_settle_s,
    )
    register_supervisor(
        node_service, supervisor, table, router=router, state=state, state_path=cfg.node.state_path,
        make_local_link=swarm.make_local_link, lease=lease, replicator=replicator, config=cfg,
    )
    register_power(
        node_service, kind=cfg.node.kind, power_controllable=cfg.node.power_controllable,
        request_restart=request_restart, router=router, blockers=[b for p in plugins for b in p.power_blockers],
    )
    register_update(node_service, repo=origin_repo_url(), emit=swarm.emit)

    try:
        await swarm.start()
        for assignment in parse_valid(assignments):
            spec = table.spec(assignment.service)
            if spec is None or spec.name in router.local_services:
                continue
            target = local_target(spec, supervisor.get(assignment.key), table, cfg, swarm.make_local_link)
            if target is not None:
                await router.add_local_service(spec.name, target)
        await replicator.start()
        await supervisor.start_all()
        await lease.start()
        log.info(
            "Нода %s запущена: службы [%s], соседи [%s]",
            cfg.node.id,
            ", ".join(supervisor.services),
            ", ".join(router.peers) or "—",
        )
        await stop_event.wait()
    finally:
        if remove_handlers is not None:
            remove_handlers()
        log.info("Останов ноды...")
        budget = ShutdownBudget(cfg.node.stop_timeout_s + SHUTDOWN_BUDGET_S)
        arm_hard_exit(budget.total_s + HARD_EXIT_MARGIN_S)
        await budget.step("прощание с роем", swarm.farewell())
        await budget.step("аренда лидерства", lease.stop())
        await budget.step("репликация настроек", replicator.stop())
        await budget.step(
            "останов служб", supervisor.stop_all(), timeout=cfg.node.stop_timeout_s + STEP_TIMEOUT_S
        )
        for name, link in list(router.local_services.items()):
            await budget.step(f"линк {name}", link.stop())
        await budget.step("рой", swarm.stop())
        if budget.overdue:
            log.error("Нода остановлена с оговорками: не уложились [%s]", ", ".join(budget.overdue))
        else:
            log.info("Нода остановлена чисто")
    return restart_requested


async def run_service(
    name: str, instance: str = "", config_path: Path | None = None, stop: asyncio.Event | None = None
) -> int:
    cfg = load_config(config_path)
    plugins = load_plugins(cfg.node.plugins)
    content = ContentStore(cfg.content_dir)
    table = ServiceTable(plugins)
    spec = table.spec(name)
    if spec is None or not spec.process:
        raise PluginError(f"нет процессной службы: {name}")
    ctx = with_instance(make_context(table.plugin_of(name), cfg, content, plugins=plugins), instance_store(cfg), name, instance)
    ctx.data_dir.mkdir(parents=True, exist_ok=True)
    own_stop = stop is None
    if own_stop:
        stop = asyncio.Event()
        remove = install_stop_handlers(asyncio.get_running_loop(), current().stop_signals(), stop.set)
    server = None
    if spec.handler is not None and spec.endpoint is not None:
        handler = spec.handler(ctx)
        server = ProtoServer(spec.endpoint(ctx.config), handler, token=swarm_section(cfg).token)
        ctx = dataclasses.replace(ctx, handler=handler, broadcast=server.broadcast_event)
    pending: list[asyncio.Task] = []
    try:
        if server is not None:
            await server.start()
        run_task = asyncio.create_task(spec.run(ctx))
        stop_task = asyncio.create_task(stop.wait())
        try:
            done, pending = await asyncio.wait({run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
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
