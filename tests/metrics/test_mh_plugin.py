from __future__ import annotations

import pytest

from sa_home_node import plugins
from sa_home_node.config import NodeConfig, NodeSection
from sa_home_node.fixups import registry
from sa_home_node.fixups.core import FixupContext
from sa_home_node.fixups.metrics import metrics_fixups
from sa_home_node.host import select as select_host
from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.metrics.hub import run_hub
from sa_home_node.metrics.plugin import PLUGIN
from sa_home_node.monitor.plugin import PLUGIN as MONITOR
from sa_home_node.plugins import Plugin, PluginError, load_plugins
from sa_home_node.services.registry import ServiceTable

HUB_FIXUPS = [
    "install-victoria-metrics",
    "metrics-runtime-dir",
    "victoria-metrics-config",
    "grafana-repo",
    "install-grafana",
    "grafana-config",
    "grafana-provisioning",
]


class FakeEP:
    def __init__(self, value: object) -> None:
        self._value = value

    def load(self) -> object:
        return self._value


def node_config(tmp_path, hub: bool) -> NodeConfig:
    node = NodeSection(id="n1", state_path=str(tmp_path / "state.json"))
    return NodeConfig(path=tmp_path / "config.toml", node=node, sections={"metrics": {"hub": hub}})


def test_plugin_definition():
    assert PLUGIN.name == "metrics"
    assert PLUGIN.config_model is MetricsConfig
    assert PLUGIN.fixups is metrics_fixups
    (service,) = PLUGIN.services
    assert (service.name, service.run, service.process, service.assignable) == (
        "metrics-hub",
        run_hub,
        False,
        False,
    )


def test_monitor_reads_metrics_section_from_metrics_config():
    assert ("metrics", MetricsConfig) in MONITOR.sections


def test_loaded_always_after_monitor_and_tasks():
    assert [plugin.name for plugin in load_plugins([])] == ["monitor", "tasks", "metrics"]


def test_other_plugins_follow(monkeypatch):
    other = Plugin(name="other")
    monkeypatch.setattr(plugins, "available", lambda: {"other": FakeEP(other)})
    assert [plugin.name for plugin in load_plugins(["other"])] == ["monitor", "tasks", "metrics", "other"]


def test_name_is_reserved(monkeypatch):
    monkeypatch.setattr(plugins, "available", lambda: {"metrics": FakeEP(Plugin(name="metrics"))})
    with pytest.raises(PluginError, match="Повтор имени плагина: metrics"):
        load_plugins(["metrics"])


@pytest.mark.parametrize(("hub", "expected"), [(True, ("metrics-hub",)), (False, ())])
def test_service_starts_only_with_hub(tmp_path, hub, expected):
    assert ServiceTable([PLUGIN]).auto_names(node_config(tmp_path, hub)) == expected
    assert ServiceTable([PLUGIN]).assignable_names() == ()


@pytest.mark.parametrize(("hub", "expected"), [(True, HUB_FIXUPS), (False, [])])
def test_fixups_only_with_hub(tmp_path, monkeypatch, hub, expected):
    monkeypatch.setattr(registry, "CORE_FIXUPS", ())
    ctx = FixupContext.build(node_config(tmp_path, hub), select_host("linux"), [PLUGIN])
    assert [fixup.id for fixup in registry.collect(ctx, [PLUGIN])] == expected
