import pytest
from pydantic import BaseModel
from support import spec

from sa_home_node.config import NodeConfig, NodeSection
from sa_home_node.plugins import Plugin, PluginError
from sa_home_node.services.registry import ServiceTable


class P1Config(BaseModel):
    port: int = 9


def plugins() -> tuple[Plugin, Plugin]:
    p1 = Plugin(
        name="p1",
        config_model=P1Config,
        services=(spec("s1", endpoint=lambda c: f"tcp://127.0.0.1:{c.port}"), spec("s2")),
    )
    p2 = Plugin(name="p2", services=(spec("s3", assignable=False), spec("s4", external=True, assignable=False)))
    return p1, p2


def test_names_lookup_and_endpoints(tmp_path) -> None:
    table = ServiceTable(plugins())
    assert table.names() == ("s1", "s2", "s3", "s4")
    assert table.assignable_names() == ("s1", "s2")
    assert table.supervised_names() == ("s1", "s2", "s3")
    assert table.plugin_of("s3").name == "p2"
    assert table.spec("missing") is None
    assert table.plugin_of("missing") is None
    config = NodeConfig(path=tmp_path / "c.toml", node=NodeSection(id="n1"), sections={"p1": {"port": 7}})
    assert table.endpoints(config) == {"s1": "tcp://127.0.0.1:7"}


@pytest.mark.parametrize(
    ("groups", "second"),
    [
        ((Plugin(name="p1", services=(spec("s1"), spec("s1"))),), "p1"),
        ((Plugin(name="p1", services=(spec("s1"),)), Plugin(name="p2", services=(spec("s1"),))), "p2"),
    ],
)
def test_duplicate(groups: tuple[Plugin, ...], second: str) -> None:
    with pytest.raises(PluginError, match=rf"Повтор имени службы: s1 \(плагины p1, {second}\)"):
        ServiceTable(groups)
