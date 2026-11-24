from collections.abc import Sequence

from sa_home_node.config import NodeConfig
from sa_home_node.plugins import Plugin, PluginError, ServiceSpec


class ServiceTable:
    def __init__(self, plugins: Sequence[Plugin]) -> None:
        self._specs: dict[str, ServiceSpec] = {}
        self._owners: dict[str, Plugin] = {}
        for plugin in plugins:
            for spec in plugin.services:
                owner = self._owners.get(spec.name)
                if owner is not None:
                    raise PluginError(
                        f"Повтор имени службы: {spec.name} (плагины {owner.name}, {plugin.name})"
                    )
                self._specs[spec.name] = spec
                self._owners[spec.name] = plugin

    def spec(self, name: str) -> ServiceSpec | None:
        return self._specs.get(name)

    def plugin_of(self, name: str) -> Plugin | None:
        return self._owners.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def assignable_names(self) -> tuple[str, ...]:
        return tuple(n for n in self._specs if self._specs[n].assignable)

    def supervised_names(self) -> tuple[str, ...]:
        return tuple(n for n in self._specs if not self._specs[n].external)

    def auto_names(self, config: NodeConfig) -> tuple[str, ...]:
        return tuple(n for n, spec in self._specs.items() if spec.auto is not None and spec.auto(config))

    def endpoints(self, config: NodeConfig) -> dict[str, str]:
        result = {}
        for name, spec in self._specs.items():
            if spec.endpoint is None:
                continue
            plugin = self._owners[name]
            section = None
            if plugin.config_model is not None:
                section = config.section(plugin.name, plugin.config_model)
            result[name] = spec.endpoint(section)
        return result
