from pathlib import Path

import pytest
from pydantic import BaseModel

from sa_home_node import plugins
from sa_home_node.config import ConfigError, NodeConfig, load_config
from sa_home_node.content import ContentError, ContentStore
from sa_home_node.fixups.core import Fixup
from sa_home_node.plugins import (
    Plugin,
    PluginError,
    ServiceContext,
    ServiceSpec,
    load_plugins,
    make_context,
)


class PlugModel(BaseModel):
    key: str = "v"


class FakeEP:
    def __init__(self, name: str, value: object) -> None:
        self.name = name
        self._value = value

    def load(self) -> object:
        return self._value


def patch_eps(monkeypatch: pytest.MonkeyPatch, eps: dict[str, FakeEP]) -> None:
    monkeypatch.setattr(plugins, "available", lambda: eps)


def write_config(tmp_path: Path, text: str) -> NodeConfig:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return load_config(path)


def test_plugin_model() -> None:
    async def run(ctx: ServiceContext) -> None:
        pass

    spec = ServiceSpec(name="svc", run=run)
    plugin = Plugin(name="plug", services=(spec,))
    assert plugin.services[0].name == "svc"
    assert plugin.config_model is None
    assert plugin.content_keys == ()
    assert plugin.fixups is None


def test_plugin_with_fixups(monkeypatch: pytest.MonkeyPatch) -> None:
    fixup = Fixup("fx-a", "T", lambda: True, lambda: None)
    plugin = Plugin(name="plug", fixups=lambda ctx: [fixup])
    patch_eps(monkeypatch, {"plug": FakeEP("plug", plugin)})
    loaded = load_plugins(["plug"])[-1]
    assert loaded.fixups(None) == [fixup]


def test_load_order(monkeypatch: pytest.MonkeyPatch) -> None:
    eps = {n: FakeEP(n, Plugin(name=n)) for n in ("a", "b")}
    patch_eps(monkeypatch, eps)
    assert [p.name for p in load_plugins(["b", "a"])] == ["monitor", "tasks", "metrics", "b", "a"]


@pytest.mark.parametrize(
    ("eps", "names", "match"),
    [
        ({"a": Plugin(name="a")}, ["x", "y"], "x, y.*установлены: a"),
        ({"a": Plugin(name="a")}, ["a", "a"], "Повтор"),
        ({"tasks": Plugin(name="tasks")}, ["tasks"], "Повтор"),
        ({"a": object()}, ["a"], "не вернула Plugin"),
        ({"a": Plugin(name="b")}, ["a"], "не совпадает"),
    ],
)
def test_load_errors(
    monkeypatch: pytest.MonkeyPatch, eps: dict[str, object], names: list[str], match: str
) -> None:
    patch_eps(monkeypatch, {n: FakeEP(n, v) for n, v in eps.items()})
    with pytest.raises(PluginError, match=match):
        load_plugins(names)


def write_content(tmp_path: Path, text: str) -> ContentStore:
    root = tmp_path / "content"
    root.mkdir()
    (root / "plug.toml").write_text(text)
    return ContentStore(root)


def test_context_with_model(tmp_path: Path) -> None:
    config = write_config(tmp_path, '[node]\nid = "n1"\n\n[plug]\nkey = "v"\n')
    content = write_content(tmp_path, 'a = 1\n')
    plugin = Plugin(name="plug", config_model=PlugModel, content_keys=("plug.a",))
    ctx = make_context(plugin, config, content)
    assert ctx.node_id == "n1"
    assert ctx.data_dir == config.data_dir / "plug"
    assert ctx.config.key == "v"
    assert ctx.content is content


def test_context_without_model(tmp_path: Path) -> None:
    config = write_config(tmp_path, '[node]\nid = "n2"\n')
    ctx = make_context(Plugin(name="plug"), config, write_content(tmp_path, ""))
    assert ctx.config is None


def test_context_bad_section(tmp_path: Path) -> None:
    config = write_config(tmp_path, '[node]\nid = "n3"\n\n[plug]\nkey = 5\n')
    plugin = Plugin(name="plug", config_model=PlugModel)
    with pytest.raises(ConfigError, match="plug"):
        make_context(plugin, config, write_content(tmp_path, ""))


def test_context_missing_content(tmp_path: Path) -> None:
    config = write_config(tmp_path, '[node]\nid = "n4"\n')
    plugin = Plugin(name="plug", content_keys=("plug.a",))
    with pytest.raises(ContentError, match="plug.a"):
        make_context(plugin, config, write_content(tmp_path, 'b = 1\n'))
