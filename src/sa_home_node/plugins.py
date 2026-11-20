from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path

from pydantic import BaseModel

from sa_home_node.config import NodeConfig
from sa_home_node.content import ContentStore
from sa_home_node.proto.actions import NodeAction
from sa_home_node.swarm.node import SwarmApi

ENTRY_POINT_GROUP = "sa_home_node.plugins"


class PluginError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceContext:
    node_id: str
    data_dir: Path
    config: BaseModel | None
    content: ContentStore
    swarm: SwarmApi | None = None


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    run: Callable[[ServiceContext], Awaitable[None]]


@dataclass(frozen=True)
class Plugin:
    name: str
    config_model: type[BaseModel] | None = None
    services: tuple[ServiceSpec, ...] = ()
    content_keys: tuple[str, ...] = ()
    node_actions: tuple[NodeAction, ...] = ()


def available() -> dict[str, EntryPoint]:
    return {ep.name: ep for ep in entry_points(group=ENTRY_POINT_GROUP)}


def load_plugins(names: Iterable[str]) -> list[Plugin]:
    names = list(names)
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise PluginError(f"Повтор имени плагина: {name}")
        seen.add(name)
    eps = available()
    unknown = [n for n in names if n not in eps]
    if unknown:
        installed = ", ".join(sorted(eps)) or "нет"
        raise PluginError(
            f"Плагины не установлены: {', '.join(unknown)}; установлены: {installed}"
        )
    result = []
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
    plugin: Plugin, config: NodeConfig, content: ContentStore, swarm: SwarmApi | None = None
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
    )
