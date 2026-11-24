from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from sa_home_node.metrics.spec import DashboardSpec, ScrapeJobSpec
from sa_home_node.plugins import Plugin

OLD_FIELDS = [
    "name",
    "config_model",
    "services",
    "content_keys",
    "node_actions",
    "power_blockers",
    "replication_hooks",
    "fixups",
    "sections",
    "sensor_providers",
    "backup",
    "task_kinds",
]


def test_plugin_without_specs() -> None:
    plugin = Plugin("p")
    assert plugin.scrape_jobs == ()
    assert plugin.dashboards == ()
    assert [f.name for f in dataclasses.fields(Plugin)][: len(OLD_FIELDS)] == OLD_FIELDS


def test_plugin_keeps_specs() -> None:
    job = ScrapeJobSpec("sa-home-extra", "extra.json", port=lambda cfg: cfg.exporter_port + 1)
    only = ScrapeJobSpec("sa-home-only", "only.json", port=lambda cfg: 9200, nodes=lambda: {"n2"})
    dash = DashboardSpec("extra.json", lambda: {"uid": "extra"})
    plugin = Plugin("p", scrape_jobs=(job, only), dashboards=(dash,))
    assert plugin.scrape_jobs == (job, only)
    assert plugin.dashboards == (dash,)
    cfg = SimpleNamespace(exporter_port=9100)
    assert [(j.name, j.targets_file, j.port(cfg), set(j.nodes())) for j in plugin.scrape_jobs] == [
        ("sa-home-extra", "extra.json", 9101, set()),
        ("sa-home-only", "only.json", 9200, {"n2"}),
    ]
    assert (dash.file, dash.build()) == ("extra.json", {"uid": "extra"})


@pytest.mark.parametrize(
    ("spec", "attr"),
    [
        (ScrapeJobSpec("j", "j.json", port=lambda cfg: 1), "name"),
        (DashboardSpec("d.json", dict), "file"),
    ],
)
def test_specs_frozen(spec, attr: str) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, attr, "x")
