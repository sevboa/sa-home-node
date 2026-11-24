from __future__ import annotations

from pathlib import Path

import pytest

from sa_home_node.metrics import provisioning
from sa_home_node.metrics.config import MetricsConfig

FIXTURES = Path(__file__).parent.parent / "fixtures" / "metrics"
CONFIG = MetricsConfig(vm_port=8429)
DASHBOARDS_DIR = Path("/var/lib/metrics-hub/dashboards")


def _fixture(name):
    return (FIXTURES / "fixups" / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("actual", "fixture"),
    [
        (lambda: provisioning.grafana_datasource_content(CONFIG), "grafana-datasource.yaml"),
        (lambda: provisioning.grafana_provider_content(DASHBOARDS_DIR), "grafana-provider.yaml"),
    ],
    ids=["datasource", "provider"],
)
def test_content_matches_fixture(actual, fixture):
    assert actual() == _fixture(fixture)


def test_provisioning_files():
    assert provisioning.grafana_provisioning_files(CONFIG, DASHBOARDS_DIR) == {
        Path("/etc/grafana/provisioning/datasources/sa-home.yaml"): _fixture("grafana-datasource.yaml"),
        Path("/etc/grafana/provisioning/dashboards/sa-home.yaml"): _fixture("grafana-provider.yaml"),
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GRAFANA_DATASOURCE_FILE", Path("/etc/grafana/provisioning/datasources/sa-home.yaml")),
        ("GRAFANA_DASHBOARD_PROVIDER_FILE", Path("/etc/grafana/provisioning/dashboards/sa-home.yaml")),
    ],
)
def test_paths(name, value):
    assert getattr(provisioning, name) == value
