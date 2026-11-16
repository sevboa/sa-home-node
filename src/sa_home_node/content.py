import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path


class ContentError(ValueError):
    pass


class ContentStore:
    def __init__(self, root: Path):
        self._root = root
        self._cache: dict[str, Mapping[str, object]] = {}

    def _plugin(self, plugin: str) -> Mapping[str, object]:
        if plugin not in self._cache:
            path = self._root / f"{plugin}.toml"
            try:
                with path.open("rb") as f:
                    self._cache[plugin] = tomllib.load(f)
            except OSError as e:
                raise ContentError(f"Файл контента не найден: {path}") from e
            except tomllib.TOMLDecodeError as e:
                raise ContentError(f"Файл контента битый: {path}: {e}") from e
        return self._cache[plugin]

    def get(self, key: str, default: object = None) -> object:
        plugin, _, name = _split(key)
        return self._plugin(plugin).get(name, default)

    def __contains__(self, key: str) -> bool:
        plugin, _, name = _split(key)
        return name in self._plugin(plugin)

    def require(self, keys: Iterable[str]) -> None:
        missing = [k for k in keys if k not in self]
        if missing:
            raise ContentError("Нет ключей контента: " + ", ".join(missing))


def _split(key: str) -> tuple[str, str, str]:
    plugin, sep, name = key.partition(".")
    if not sep or not plugin or not name:
        raise ContentError(f"Ключ контента должен быть вида '<плагин>.<имя>': {key!r}")
    return plugin, sep, name
