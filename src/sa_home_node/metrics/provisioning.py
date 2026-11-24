from __future__ import annotations

from pathlib import Path

from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.metrics.names import DATASOURCE_TYPE, DATASOURCE_UID

GRAFANA_DATASOURCE_FILE = Path("/etc/grafana/provisioning/datasources/sa-home.yaml")
GRAFANA_DASHBOARD_PROVIDER_FILE = Path("/etc/grafana/provisioning/dashboards/sa-home.yaml")


def grafana_datasource_content(config: MetricsConfig) -> str:
    return (
        "apiVersion: 1\n"
        "datasources:\n"
        "  - name: VictoriaMetrics\n"
        f"    uid: {DATASOURCE_UID}\n"
        f"    type: {DATASOURCE_TYPE}\n"
        "    access: proxy\n"
        f"    url: http://127.0.0.1:{config.vm_port}\n"
        "    isDefault: true\n"
    )


def grafana_provider_content(dashboards_dir: Path) -> str:
    return (
        "apiVersion: 1\n"
        "providers:\n"
        "  - name: sa-home\n"
        "    type: file\n"
        "    allowUiUpdates: false\n"
        "    updateIntervalSeconds: 30\n"
        "    options:\n"
        f"      path: {dashboards_dir}\n"
        "      foldersFromFilesStructure: true\n"
    )


def grafana_provisioning_files(config: MetricsConfig, dashboards_dir: Path) -> dict[Path, str]:
    return {
        GRAFANA_DATASOURCE_FILE: grafana_datasource_content(config),
        GRAFANA_DASHBOARD_PROVIDER_FILE: grafana_provider_content(dashboards_dir),
    }
