import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, Field, ValidationError, field_validator

from sa_home_node.swarm.kind import NodeKind

DEFAULT_CONFIG = Path("~/.config/sa-home-bot/config.toml")

T = TypeVar("T", bound=BaseModel)


class ConfigError(ValueError):
    pass


class NodeSection(BaseModel):
    id: str
    kind: NodeKind = "workstation"
    data_dir: Path = Path("data")
    content_dir: Path | None = None
    plugins: list[str] = []
    socket: str = "./data/node.sock"
    listen: list[str] = []
    state_path: str = "./data/node-state.json"
    assignments: list[str] = []
    restart_delay_s: float = Field(default=5.0, gt=0)
    stop_timeout_s: float = Field(default=90.0, gt=0)
    power_controllable: bool | None = None

    @field_validator("listen", mode="before")
    @classmethod
    def _listen_as_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value


@dataclass
class NodeConfig:
    path: Path
    node: NodeSection
    sections: dict[str, dict]

    @property
    def _base(self) -> Path:
        return self.path.parent

    @property
    def data_dir(self) -> Path:
        d = self.node.data_dir
        return d if d.is_absolute() else Path.cwd() / d

    @property
    def content_dir(self) -> Path:
        d = self.node.content_dir
        if d is None:
            return self._base / "content"
        return d if d.is_absolute() else self._base / d

    @property
    def socket_path(self) -> Path:
        s = Path(self.node.socket)
        return s if s.is_absolute() else Path.cwd() / s

    @property
    def state_file(self) -> Path:
        s = Path(self.node.state_path)
        return s if s.is_absolute() else Path.cwd() / s

    def section(self, name: str, model: type[T]) -> T:
        raw = self.sections.get(name, {})
        try:
            return model.model_validate(raw)
        except ValidationError as e:
            raise ConfigError(f"Секция [{name}] не прошла проверку: {e}") from e


def load_config(path: Path | None = None) -> NodeConfig:
    path = Path(path or DEFAULT_CONFIG).expanduser()
    if not path.is_file():
        raise ConfigError(f"Файл конфига не найден: {path}")
    with path.open("rb") as f:
        data: dict[str, Any] = tomllib.load(f)
    node = data.pop("node", None)
    if not isinstance(node, dict) or not str(node.get("id", "")).strip():
        raise ConfigError(f"В конфиге нет секции [node] с полем id: {path}")
    try:
        section = NodeSection.model_validate(node)
    except ValidationError as e:
        raise ConfigError(f"Секция [node] не прошла проверку: {e}") from e
    return NodeConfig(path=path, node=section, sections=data)
