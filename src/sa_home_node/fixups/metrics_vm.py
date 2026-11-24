from __future__ import annotations

import os
import stat
from getpass import getuser
from pathlib import Path

from sa_home_node.fixups.core import Fixup, FixupContext
from sa_home_node.fixups.needs import metrics_hub
from sa_home_node.fixups.privileged import (
    enable_now,
    file_matches,
    find_executable,
    install_package,
    install_root_file,
    path_exists,
    read_text_if_exists,
    run_sudo,
    unit_is_active,
)
from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.metrics.runtime import dashboards_dir, scrape_jobs, targets_dir
from sa_home_node.metrics.vm import (
    VM_DEFAULTS_FILE,
    VM_PACKAGE,
    VM_SCRAPE_FILE,
    VM_UNIT,
    VM_USER,
    vm_defaults_content,
    vm_scrape_content,
)

RUNTIME_DIR_MODE = 0o755


def _owned_by_current_user(path: Path) -> bool:
    try:
        info = path.stat()
    except OSError:
        return False
    return info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == RUNTIME_DIR_MODE


def vm_fixups(ctx: FixupContext) -> list[Fixup]:
    config = ctx.section("metrics", MetricsConfig)
    root = Path(config.runtime_dir)
    subdirs = [targets_dir(config), dashboards_dir(config)]
    jobs = scrape_jobs(config, ctx.plugins)

    def runtime_apply() -> None:
        user = getuser()
        run_sudo(["install", "-d", "-m", "0755", "-o", user, "-g", user, str(root)])
        run_sudo(["install", "-d", "-m", "0755", "-o", user, "-g", user, *[str(p) for p in subdirs]])

    def config_check() -> bool:
        return (
            path_exists(Path(config.data_dir))
            and file_matches(VM_DEFAULTS_FILE, vm_defaults_content(config, VM_SCRAPE_FILE))
            and file_matches(VM_SCRAPE_FILE, vm_scrape_content(config, jobs))
            and unit_is_active(VM_UNIT)
        )

    def config_apply() -> None:
        run_sudo(["install", "-d", "-m", "0750", "-o", VM_USER, "-g", VM_USER, config.data_dir])
        install_root_file(VM_SCRAPE_FILE, vm_scrape_content(config, jobs), mode="0644", make_parents=True)
        previous = read_text_if_exists(VM_DEFAULTS_FILE)
        defaults = vm_defaults_content(config, VM_SCRAPE_FILE)
        if previous != defaults:
            install_root_file(VM_DEFAULTS_FILE, defaults, mode="0644")
        enable_now(VM_UNIT)
        if previous != defaults:
            run_sudo(["systemctl", "restart", VM_UNIT])

    return [
        Fixup(
            id="install-victoria-metrics",
            title="Установить victoria-metrics (хранилище метрик хаба)",
            needed=metrics_hub,
            check=lambda: find_executable(VM_PACKAGE) is not None,
            apply=lambda: install_package(VM_PACKAGE, "неизвестен пакетный менеджер для victoria-metrics"),
        ),
        Fixup(
            id="metrics-runtime-dir",
            title=f"Создать каталог целей и дашбордов метрик {root} (владелец — пользователь ноды)",
            needed=metrics_hub,
            check=lambda: all(_owned_by_current_user(path) for path in [root, *subdirs]),
            apply=runtime_apply,
        ),
        Fixup(
            id="victoria-metrics-config",
            title=f"Настроить victoria-metrics (данные {config.data_dir})",
            needed=metrics_hub,
            check=config_check,
            apply=config_apply,
        ),
    ]
