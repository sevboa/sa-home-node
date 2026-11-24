from __future__ import annotations

from pathlib import Path

from sa_home_node.fixups.core import Fixup, FixupContext
from sa_home_node.fixups.needs import metrics_hub
from sa_home_node.fixups.privileged import file_matches, install_root_file, path_exists, run_sudo
from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.metrics.provisioning import grafana_provisioning_files
from sa_home_node.metrics.runtime import dashboards_dir

GRAFANA_UNIT = "grafana-server.service"
LEGACY_GRAFANA_DASHBOARD_DIR = Path("/etc/grafana/sa-home-dashboards")


def grafana_provisioning_fixup(ctx: FixupContext) -> Fixup:
    config = ctx.section("metrics", MetricsConfig)
    files = grafana_provisioning_files(config, dashboards_dir(config))

    def check() -> bool:
        return all(file_matches(path, content) for path, content in files.items()) and not path_exists(
            LEGACY_GRAFANA_DASHBOARD_DIR
        )

    def apply() -> None:
        for path, content in files.items():
            install_root_file(path, content, mode="0644", make_parents=True)
        run_sudo(["rm", "-rf", "--", str(LEGACY_GRAFANA_DASHBOARD_DIR)])
        run_sudo(["systemctl", "restart", GRAFANA_UNIT])

    return Fixup(
        id="grafana-provisioning",
        title="Выложить источник данных и провайдер дашбордов grafana",
        check=check,
        apply=apply,
        needed=metrics_hub,
    )
