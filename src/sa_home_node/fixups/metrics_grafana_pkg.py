from __future__ import annotations

from pathlib import Path

from sa_home_node.fixups import needs
from sa_home_node.fixups.core import Fixup
from sa_home_node.fixups.privileged import (
    file_matches,
    find_executable,
    install_from,
    install_package,
    install_root_file,
    path_exists,
    run_sudo,
    run_user,
    temp_file,
)

GRAFANA_KEY_URL = "https://apt.grafana.com/gpg.key"
GRAFANA_KEY_PATH = Path("/etc/apt/keyrings/grafana.asc")
GRAFANA_LIST_PATH = Path("/etc/apt/sources.list.d/grafana.list")
GRAFANA_LIST_CONTENT = f"deb [signed-by={GRAFANA_KEY_PATH}] https://apt.grafana.com stable main\n"
GRAFANA_PACKAGE = "grafana"


def _grafana_repo_check() -> bool:
    return path_exists(GRAFANA_KEY_PATH) and file_matches(GRAFANA_LIST_PATH, GRAFANA_LIST_CONTENT)


def _grafana_repo_apply() -> None:
    with temp_file("") as tmp:
        run_user(["curl", "-fsSL", GRAFANA_KEY_URL, "-o", str(tmp)], timeout=60)
        install_from(tmp, GRAFANA_KEY_PATH, mode="0644", make_parents=True)
    install_root_file(GRAFANA_LIST_PATH, GRAFANA_LIST_CONTENT, mode="0644")
    run_sudo(["apt-get", "update"])


def _grafana_installed_check() -> bool:
    return find_executable("grafana-server") is not None


def grafana_pkg_fixups() -> list[Fixup]:
    return [
        Fixup(
            id="grafana-repo",
            title="Подключить репозиторий apt.grafana.com",
            needed=needs.metrics_hub,
            check=_grafana_repo_check,
            apply=_grafana_repo_apply,
        ),
        Fixup(
            id="install-grafana",
            title="Установить grafana",
            needed=needs.metrics_hub,
            check=_grafana_installed_check,
            apply=lambda: install_package(GRAFANA_PACKAGE, "неизвестен пакетный менеджер для grafana"),
        ),
    ]
