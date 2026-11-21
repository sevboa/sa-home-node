from pathlib import Path

import pytest
from pydantic import BaseModel

from sa_home_node.config import NodeConfig, load_config
from sa_home_node.plugins import Plugin, PluginError, ServiceSpec
from sa_home_node.services.registry import ServiceTable


class P1Config(BaseModel):
    port: int = 9


def spec(name: str, **kwargs: object) -> ServiceSpec:
    async def run(ctx: object) -> None:
        pass

    return ServiceSpec(name=name, run=run, **kwargs)


def p1() -> Plugin:
    return Plugin(
        name="p1",
        config_model=P1Config,
        services=(
            spec("s1", endpoint=lambda c: f"tcp://127.0.0.1:{c.port}"),
            spec("s2"),
        ),
    )


def p2() -> Plugin:
    return Plugin(name="p2", services=(spec("s3", assignable=False), spec("s4", external=True, assignable=False)))


def write_config(tmp_path: Path, text: str) -> NodeConfig:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return load_config(path)


def test_orders() -> None:
    table = ServiceTable((p1(), p2()))
    assert table.names() == ("s1", "s2", "s3", "s4")
    assert table.assignable_names() == ("s1", "s2")
    assert table.supervised_names() == ("s1", "s2", "s3")


def test_spec_and_plugin_of() -> None:
    table = ServiceTable((p1(), p2()))
    assert table.plugin_of("s3").name == "p2"
    assert table.spec("missing") is None
    assert table.plugin_of("missing") is None


def test_endpoints(tmp_path: Path) -> None:
    config = write_config(tmp_path, '[node]\nid = "n1"\n\n[p1]\nport = 7\n')
    table = ServiceTable((p1(), p2()))
    assert table.endpoints(config) == {"s1": "tcp://127.0.0.1:7"}


@pytest.mark.parametrize(
    ("plugins", "match"),
    [
        (
            (Plugin(name="p1", services=(spec("s1"), spec("s1"))),),
            "Повтор имени службы: s1 (плагины p1, p1)",
        ),
        (
            (
                Plugin(name="p1", services=(spec("s1"),)),
                Plugin(name="p2", services=(spec("s1"),)),
            ),
            "Повтор имени службы: s1 (плагины p1, p2)",
        ),
    ],
)
def test_duplicate(plugins: tuple[Plugin], match: str) -> None:
    match = match.replace("(", r"\(").replace(")", r"\)")
    with pytest.raises(PluginError, match=match):
        ServiceTable(plugins)
