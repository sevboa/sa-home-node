from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from sa_home_node.metrics.catalog import dashboards
from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.metrics.filesd import file_sd_text
from sa_home_node.metrics.names import JOB_NODES
from sa_home_node.metrics.panels import render_dashboard
from sa_home_node.metrics.scrapeconf import ScrapeJob
from sa_home_node.metrics.targets import scrape_targets
from sa_home_node.plugins import Plugin
from sa_home_node.swarm.config import SwarmPeer

TARGETS_SUBDIR = "targets"
DASHBOARDS_SUBDIR = "dashboards"
NODES_TARGETS_FILE = "nodes.json"


def targets_dir(config: MetricsConfig) -> Path:
    return Path(config.runtime_dir) / TARGETS_SUBDIR


def dashboards_dir(config: MetricsConfig) -> Path:
    return Path(config.runtime_dir) / DASHBOARDS_SUBDIR


def scrape_jobs(config: MetricsConfig, plugins: Sequence[Plugin]) -> list[ScrapeJob]:
    tdir = targets_dir(config)
    jobs: list[ScrapeJob] = [ScrapeJob(JOB_NODES, str(tdir / NODES_TARGETS_FILE))]
    for plugin in plugins:
        for spec in plugin.scrape_jobs:
            jobs.append(ScrapeJob(spec.name, str(tdir / spec.targets_file)))
    return jobs


def runtime_files(
    config: MetricsConfig,
    own_id: str,
    own_host: str | None,
    peers: Sequence[SwarmPeer],
    plugins: Sequence[Plugin],
) -> dict[Path, str]:
    tdir = targets_dir(config)
    ddir = dashboards_dir(config)

    files: dict[Path, str] = {}

    targets = scrape_targets(own_id, own_host, peers)
    files[tdir / NODES_TARGETS_FILE] = file_sd_text(targets, config.exporter_port)

    for plugin in plugins:
        for spec in plugin.scrape_jobs:
            spec_nodes = set(spec.nodes())
            if spec_nodes:
                filtered = [t for t in targets if t.node in spec_nodes]
            else:
                filtered = targets
            port = spec.port(config)
            files[tdir / spec.targets_file] = file_sd_text(filtered, port)

    for file_name, build in dashboards(plugins).items():
        files[ddir / file_name] = render_dashboard(build())

    return files
