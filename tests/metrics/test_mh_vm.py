from __future__ import annotations

from pathlib import Path

import pytest

from sa_home_node.metrics import vm
from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.metrics.scrapeconf import ScrapeJob

FIXTURES = Path(__file__).parent.parent / "fixtures" / "metrics"
CONFIG = MetricsConfig(
    data_dir="/srv/metrics",
    retention="90d",
    vm_port=8429,
    scrape_interval="15s",
    runtime_dir="/var/lib/metrics-hub",
)
JOBS = [
    ScrapeJob("sa-home-nodes", "/var/lib/metrics-hub/targets/nodes.json"),
    ScrapeJob("sa-home-extra", "/var/lib/metrics-hub/targets/extra.json"),
]


@pytest.mark.parametrize(
    ("actual", "fixture"),
    [
        (lambda: vm.vm_defaults_content(CONFIG, vm.VM_SCRAPE_FILE), "vm-defaults.txt"),
        (lambda: vm.vm_scrape_content(CONFIG, JOBS), "vm-scrape.yml"),
    ],
    ids=["defaults", "scrape"],
)
def test_content_matches_fixture(actual, fixture):
    assert actual() == (FIXTURES / "fixups" / fixture).read_text(encoding="utf-8")


def test_vm_args_order_and_listen_address():
    flags = vm.vm_args(CONFIG, Path("/x/scrape.yml")).split(" ")
    assert [flag.split("=")[0] for flag in flags] == [
        "-storageDataPath",
        "-retentionPeriod",
        "-httpListenAddr",
        "-promscrape.config",
        "-promscrape.configCheckInterval",
        "-promscrape.fileSDCheckInterval",
    ]
    assert "-httpListenAddr=127.0.0.1:8429" in flags
    assert "-promscrape.config=/x/scrape.yml" in flags


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("VM_PACKAGE", "victoria-metrics"),
        ("VM_UNIT", "victoria-metrics.service"),
        ("VM_USER", "_victoria-metrics"),
        ("VM_DEFAULTS_FILE", Path("/etc/default/victoria-metrics")),
        ("VM_SCRAPE_FILE", Path("/etc/victoria-metrics/scrape.yml")),
    ],
)
def test_constants(name, value):
    assert getattr(vm, name) == value
