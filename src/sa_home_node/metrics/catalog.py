from __future__ import annotations

from collections.abc import Callable, Sequence

from sa_home_node.metrics.nodes_dashboard import nodes_dashboard
from sa_home_node.plugins import Plugin


def dashboards(plugins: Sequence[Plugin]) -> dict[str, Callable[[], dict]]:
    result: dict[str, Callable[[], dict]] = {"nodes.json": nodes_dashboard}

    for plugin in plugins:
        for spec in plugin.dashboards:
            file = spec.file
            if not file.endswith(".json"):
                raise ValueError(f"Имя файла дашборда должно оканчиваться на .json: {file!r}")
            if "/" in file:
                raise ValueError(f"Имя файла дашборда не может содержать '/': {file!r}")
            if file in result:
                raise ValueError(f"Повтор имени файла дашборда: {file!r}")
            result[file] = spec.build

    return result
