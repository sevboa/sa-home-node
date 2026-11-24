from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path

from pydantic import BaseModel

from sa_home_node.backup.spec import BackupSpec
from sa_home_node.bot.spec import BotSpec
from sa_home_node.config import NodeConfig
from sa_home_node.content import ContentStore
from sa_home_node.fixups.core import Fixup, FixupContext
from sa_home_node.metrics.spec import DashboardSpec, ScrapeJobSpec
from sa_home_node.monitor.providers import SensorProvider
from sa_home_node.proto.actions import NodeAction
from sa_home_node.proto.server import ServiceHandler
from sa_home_node.services.instances import InstanceMeta
from sa_home_node.swarm.config import swarm_section
from sa_home_node.swarm.node import SwarmApi
from sa_home_node.tasks.kinds import TaskKind

ENTRY_POINT_GROUP = "sa_home_node.plugins"


class PluginError(RuntimeError):
    pass


class ServiceFenced(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceContext:
    node_id: str
    data_dir: Path
    config: BaseModel | None
    content: ContentStore
    swarm: SwarmApi | None = None
    instance: str = ""
    package_path: Path | None = None
    node_kind: str = ""
    sections: Mapping[str, BaseModel] = field(default_factory=dict)
    plugins: tuple[Plugin, ...] = ()
    broadcast: Callable[[str, dict], Awaitable[int]] | None = None
    handler: ServiceHandler | None = None
    node_socket: str = ""
    swarm_token: str = ""


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    run: Callable[[ServiceContext], Awaitable[None]]
    handler: Callable[[ServiceContext], ServiceHandler] | None = None
    process: bool = False
    endpoint: Callable[[BaseModel | None], str] | None = None
    assignable: bool = True
    external: bool = False
    singleton: bool = False
    instanced: bool = False
    satellites: tuple[str, ...] = ()
    auto: Callable[[NodeConfig], bool] | None = None


@dataclass(frozen=True)
class ReplicationHooks:
    passive: Callable[[str, str], bool] | None = None
    before_announce: Callable[[], Awaitable[object]] | None = None
    on_applied: Callable[[InstanceMeta, bytes], None] | None = None


@dataclass(frozen=True)
class Plugin:
    name: str
    config_model: type[BaseModel] | None = None
    services: tuple[ServiceSpec, ...] = ()
    content_keys: tuple[str, ...] = ()
    node_actions: tuple[NodeAction, ...] = ()
    power_blockers: tuple[Callable[[], Awaitable[list[str]]], ...] = ()
    replication_hooks: ReplicationHooks | None = None
    fixups: Callable[[FixupContext], Sequence[Fixup]] | None = None
    sections: tuple[tuple[str, type[BaseModel]], ...] = ()
    sensor_providers: tuple[Callable[[ServiceContext], SensorProvider], ...] = ()
    backup: BackupSpec | None = None
    task_kinds: tuple[TaskKind, ...] = ()
    scrape_jobs: tuple[ScrapeJobSpec, ...] = ()
    dashboards: tuple[DashboardSpec, ...] = ()
    bot: BotSpec | None = None


def available() -> dict[str, EntryPoint]:
    return {ep.name: ep for ep in entry_points(group=ENTRY_POINT_GROUP)}


def load_plugins(names: Iterable[str]) -> list[Plugin]:
    from sa_home_node.metrics.plugin import PLUGIN as METRICS
    from sa_home_node.monitor.plugin import PLUGIN as MONITOR
    from sa_home_node.tasks.plugin import PLUGIN as TASKS

    names = list(names)
    seen: set[str] = {MONITOR.name, TASKS.name, METRICS.name}
    for name in names:
        if name in seen:
            raise PluginError(f"Повтор имени плагина: {name}")
        seen.add(name)
    eps = available()
    unknown = [n for n in names if n not in eps]
    if unknown:
        installed = ", ".join(sorted(eps)) or "нет"
        raise PluginError(f"Плагины не установлены: {', '.join(unknown)}; установлены: {installed}")
    result = [MONITOR, TASKS, METRICS]
    for name in names:
        ep = eps[name]
        plugin = ep.load()
        if not isinstance(plugin, Plugin):
            raise PluginError(f"Точка входа '{name}' не вернула Plugin: {plugin!r}")
        if plugin.name != name:
            raise PluginError(f"Имя плагина не совпадает с точкой входа: {plugin.name!r} != {name!r}")
        result.append(plugin)
    return result


def make_context(
    plugin: Plugin,
    config: NodeConfig,
    content: ContentStore,
    swarm: SwarmApi | None = None,
    plugins: Sequence[Plugin] = (),
) -> ServiceContext:
    section = None
    if plugin.config_model is not None:
        section = config.section(plugin.name, plugin.config_model)
    content.require(plugin.content_keys)
    return ServiceContext(
        node_id=config.node.id,
        data_dir=config.data_dir / plugin.name,
        config=section,
        content=content,
        swarm=swarm,
        node_kind=config.node.kind,
        sections={name: config.section(name, model) for name, model in plugin.sections},
        plugins=tuple(plugins),
        node_socket=str(config.socket_path),
        swarm_token=swarm_section(config).token,
    )
