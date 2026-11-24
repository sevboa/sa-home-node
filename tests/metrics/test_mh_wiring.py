from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.metrics.hub import MetricsHub
from sa_home_node.metrics.runtime import scrape_jobs
from sa_home_node.metrics.spec import DashboardSpec, ScrapeJobSpec
from sa_home_node.metrics.vm import vm_scrape_content
from sa_home_node.plugins import Plugin
from sa_home_node.swarm.config import SwarmPeer

WIRING = json.loads(
    (Path(__file__).parents[1] / "fixtures/metrics/runtime/wiring.json").read_text(encoding="utf-8")
)
EXTRA = Plugin(
    name="extra",
    scrape_jobs=(
        ScrapeJobSpec(
            "sa-home-extra",
            "extra.json",
            lambda cfg: cfg.model_extra["extra_port"],
            lambda: WIRING["extra_nodes"],
        ),
    ),
    dashboards=(DashboardSpec("extra.json", lambda: WIRING["extra_dashboard"]),),
)


def make_config(tmp_path: Path) -> MetricsConfig:
    return MetricsConfig(**{**WIRING["config"], "runtime_dir": str(tmp_path), "hub": True})


async def test_tick_writes_runtime_files(tmp_path):
    ctx = SimpleNamespace(
        node_id=WIRING["own_id"],
        config=make_config(tmp_path),
        swarm=SimpleNamespace(peers=lambda: [SwarmPeer(**row) for row in WIRING["peers"]]),
        plugins=(EXTRA,),
    )
    await MetricsHub(ctx, address=lambda: WIRING["own_host"]).tick()
    written = {
        str(p.relative_to(tmp_path)): p.read_text(encoding="utf-8")
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    assert written == WIRING["files"]


def test_scrape_config_has_core_and_plugin_jobs(tmp_path):
    config = make_config(tmp_path)
    expected = WIRING["scrape_yml"].replace(WIRING["config"]["runtime_dir"], str(tmp_path))
    assert vm_scrape_content(config, scrape_jobs(config, (EXTRA,))) == expected
