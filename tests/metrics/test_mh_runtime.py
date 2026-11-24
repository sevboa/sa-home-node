from __future__ import annotations

import json
from pathlib import Path

import pytest

from sa_home_node.metrics import runtime
from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.metrics.filesd import file_sd_text
from sa_home_node.metrics.scrapeconf import ScrapeJob, scrape_config_text
from sa_home_node.metrics.spec import DashboardSpec, ScrapeJobSpec
from sa_home_node.metrics.targets import ScrapeTarget
from sa_home_node.plugins import Plugin
from sa_home_node.swarm.config import SwarmPeer

FIXTURES = Path(__file__).parent.parent / "fixtures" / "metrics"
CORE = json.loads((FIXTURES / "runtime" / "files-core.json").read_text(encoding="utf-8"))
WIRING = json.loads((FIXTURES / "runtime" / "wiring.json").read_text(encoding="utf-8"))
PEERS = [SwarmPeer(**row) for row in CORE["peers"]]
ROOT = Path(CORE["config"]["runtime_dir"])
NODES_TEXT = CORE["files"]["targets/nodes.json"]
CORE_CONFIG = MetricsConfig(**CORE["config"])
WIRING_CONFIG = MetricsConfig(**WIRING["config"])


def build_extra() -> dict:
    return WIRING["extra_dashboard"]


def stub(nodes=lambda: WIRING["extra_nodes"]) -> Plugin:
    job = ScrapeJobSpec("sa-home-extra", "extra.json", lambda config: config.model_extra["extra_port"], nodes)
    return Plugin(name="extra", scrape_jobs=(job,), dashboards=(DashboardSpec("extra.json", build_extra),))


def relative(files: dict[Path, str]) -> dict[str, str]:
    return {str(path.relative_to(ROOT)): text for path, text in files.items()}


def files_for(config: MetricsConfig, plugins=(), own_host=CORE["own_host"]) -> dict[str, str]:
    return relative(runtime.runtime_files(config, CORE["own_id"], own_host, PEERS, plugins))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TARGETS_SUBDIR", "targets"),
        ("DASHBOARDS_SUBDIR", "dashboards"),
        ("NODES_TARGETS_FILE", "nodes.json"),
    ],
)
def test_constants(name, value):
    assert getattr(runtime, name) == value


def test_directories():
    assert runtime.targets_dir(CORE_CONFIG) == ROOT / "targets"
    assert runtime.dashboards_dir(CORE_CONFIG) == ROOT / "dashboards"


def test_core_files_match_fixture():
    assert files_for(CORE_CONFIG) == CORE["files"]


def test_plugin_files_match_fixture():
    assert files_for(WIRING_CONFIG, [stub()]) == WIRING["files"]


@pytest.mark.parametrize(
    ("nodes", "expected"),
    [
        (lambda: (), NODES_TEXT.replace(":9100", ":9101")),
        (lambda: {"n9"}, "[]\n"),
        (
            lambda: {"n1", "n3"},
            file_sd_text([ScrapeTarget("n1", "100.64.0.1"), ScrapeTarget("n3", "192.168.1.3")], 9101),
        ),
    ],
    ids=["empty-means-all-peers", "unknown-node", "intersection"],
)
def test_plugin_nodes_filter(nodes, expected):
    assert files_for(WIRING_CONFIG, [stub(nodes)])["targets/extra.json"] == expected


def test_exporter_port_goes_to_nodes_file():
    config = MetricsConfig(**{**CORE["config"], "exporter_port": 9200})
    assert files_for(config)["targets/nodes.json"] == NODES_TEXT.replace(":9100", ":9200")


def test_scrape_jobs():
    assert runtime.scrape_jobs(WIRING_CONFIG, []) == [
        ScrapeJob("sa-home-nodes", str(ROOT / "targets" / "nodes.json"))
    ]
    jobs = runtime.scrape_jobs(WIRING_CONFIG, [stub()])
    assert jobs == [
        ScrapeJob("sa-home-nodes", str(ROOT / "targets" / "nodes.json")),
        ScrapeJob("sa-home-extra", str(ROOT / "targets" / "extra.json")),
    ]
    assert scrape_config_text(jobs, WIRING["config"]["scrape_interval"]) == WIRING["scrape_yml"]
