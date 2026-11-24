from __future__ import annotations

import pytest

from sa_home_node.metrics.catalog import dashboards
from sa_home_node.metrics.nodes_dashboard import nodes_dashboard
from sa_home_node.metrics.spec import DashboardSpec
from sa_home_node.plugins import Plugin


def build_extra() -> dict:
    return {"uid": "extra"}


def build_other() -> dict:
    return {"uid": "other"}


def plugin(name: str, *files: str) -> Plugin:
    return Plugin(name=name, dashboards=tuple(DashboardSpec(file, build_extra) for file in files))


def test_core_only():
    assert dashboards([]) == {"nodes.json": nodes_dashboard}
    assert dashboards([Plugin(name="p")]) == {"nodes.json": nodes_dashboard}


def test_plugin_dashboards_follow_core_in_plugin_order():
    plugins = [
        Plugin(name="a", dashboards=(DashboardSpec("extra.json", build_extra),)),
        Plugin(name="b", dashboards=(DashboardSpec("other.json", build_other),)),
    ]
    result = dashboards(plugins)
    assert list(result) == ["nodes.json", "extra.json", "other.json"]
    assert result["extra.json"] is build_extra
    assert result["other.json"] is build_other


@pytest.mark.parametrize(
    "plugins",
    [
        [plugin("a", "nodes.json")],
        [plugin("a", "extra.json"), plugin("b", "extra.json")],
        [plugin("a", "extra.json", "extra.json")],
        [plugin("a", "extra.txt")],
        [plugin("a", "dir/extra.json")],
        [plugin("a", "")],
    ],
    ids=[
        "clash-with-core",
        "clash-between-plugins",
        "clash-inside-plugin",
        "no-json-suffix",
        "subdirectory",
        "empty",
    ],
)
def test_invalid_file_names(plugins):
    with pytest.raises(ValueError, match="[а-яА-Я]"):
        dashboards(plugins)
