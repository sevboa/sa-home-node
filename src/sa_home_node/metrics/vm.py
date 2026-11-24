from __future__ import annotations

from pathlib import Path

from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.metrics.scrapeconf import ScrapeJob, scrape_config_text

VM_PACKAGE = "victoria-metrics"
VM_UNIT = "victoria-metrics.service"
VM_USER = "_victoria-metrics"
VM_DEFAULTS_FILE = Path("/etc/default/victoria-metrics")
VM_SCRAPE_FILE = Path("/etc/victoria-metrics/scrape.yml")


def vm_args(config: MetricsConfig, scrape_file: Path) -> str:
    return " ".join(
        [
            f"-storageDataPath={config.data_dir}",
            f"-retentionPeriod={config.retention}",
            f"-httpListenAddr=127.0.0.1:{config.vm_port}",
            f"-promscrape.config={scrape_file}",
            "-promscrape.configCheckInterval=1m",
            "-promscrape.fileSDCheckInterval=30s",
        ]
    )


def vm_defaults_content(config: MetricsConfig, scrape_file: Path) -> str:
    return f'ARGS="{vm_args(config, scrape_file)}"\n'


def vm_scrape_content(config: MetricsConfig, jobs: list[ScrapeJob]) -> str:
    return scrape_config_text(jobs, config.scrape_interval)
