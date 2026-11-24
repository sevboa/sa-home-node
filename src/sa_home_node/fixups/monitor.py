from __future__ import annotations

import logging
import os
import subprocess
from getpass import getuser
from pathlib import Path

from sa_home_node.fixups import needs
from sa_home_node.fixups.core import Fixup, FixupContext, FixupError
from sa_home_node.fixups.privileged import (
    SUDOERS_DIR,
    find_executable,
    install_package,
    install_sudoers_snippet,
    path_exists,
    run_sudo,
)
from sa_home_node.fixups.unit import rewrite_unit_path_line, unit_path_first_dir
from sa_home_node.monitor.config import SensorsConfig, apply_node_kind

log = logging.getLogger(__name__)

SMARTCTL_SUDOERS_FILE = "50-sa-home-node-smartctl"
SMARTMONTOOLS_PACKAGE = "smartmontools"
JOURNAL_GROUP = "systemd-journal"


def _wrapper_path() -> Path:
    return Path.home() / ".local" / "bin" / "smartctl"


def real_smartctl_path() -> str | None:
    return find_executable("smartctl", exclude_dir=str(_wrapper_path().parent))


def smartctl_sudoers_content(smartctl_path: str, user: str) -> str:
    return f"{user} ALL=(root) NOPASSWD: {smartctl_path} *\n"


def smartctl_wrapper_content(real_path: str) -> str:
    return f'#!/bin/sh\nexec sudo -n {real_path} "$@"\n'


def _disks_needed(ctx: FixupContext) -> bool:
    sensors = apply_node_kind(ctx.section("sensors", SensorsConfig), ctx.config.node.kind)
    return ctx.assigned("monitor") and sensors.disks.enabled


def _in_group(group: str) -> bool:
    try:
        out = subprocess.run(["id", "-nG", getuser()], capture_output=True, text=True, check=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return group in out.stdout.split()


def _sudoers_apply() -> None:
    path = real_smartctl_path()
    if path is None:
        raise FixupError("smartctl не найден в PATH — сначала install-smartmontools")
    if not path_exists(SUDOERS_DIR / SMARTCTL_SUDOERS_FILE):
        install_sudoers_snippet(SMARTCTL_SUDOERS_FILE, smartctl_sudoers_content(path, getuser()))
    wrapper = _wrapper_path()
    if not wrapper.exists():
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text(smartctl_wrapper_content(path))
        wrapper.chmod(0o755)


def _journal_apply() -> None:
    run_sudo(["usermod", "-aG", JOURNAL_GROUP, getuser()])
    log.warning(
        "Группа %s добавлена; чтобы нода её увидела, выполните: "
        "sudo systemctl restart user@%s.service (перезапустит службы пользователя, "
        "в том числе ноду)",
        JOURNAL_GROUP,
        os.getuid(),
    )


def monitor_fixups(ctx: FixupContext) -> list[Fixup]:
    unit = ctx.host.node_unit(ctx.config.path)

    def unit_check() -> bool:
        if not unit.path.exists():
            return True
        return unit_path_first_dir(unit.path.read_text()) == str(_wrapper_path().parent)

    def unit_apply() -> None:
        content = rewrite_unit_path_line(unit.path.read_text(), str(_wrapper_path().parent))
        if content is None:
            raise FixupError(f"в {unit.path} нет строки Environment=PATH= — правьте руками")
        unit.path.write_text(content)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        result = subprocess.run(["systemctl", "--user", "restart", unit.path.name], check=False)
        if result.returncode != 0:
            log.warning(
                "PATH юнита поправлен, но `systemctl --user restart %s` не сработал — "
                "перезапустите ноду вручную",
                unit.path.name,
            )

    return [
        Fixup(
            id="install-smartmontools",
            title="Установить smartmontools",
            needed=_disks_needed,
            check=lambda: find_executable("smartctl") is not None,
            apply=lambda: install_package(
                SMARTMONTOOLS_PACKAGE, "не найден известный пакетный менеджер для smartmontools"
            ),
        ),
        Fixup(
            id="smartctl-sudoers",
            title="Разрешить smartctl без пароля (sudoers + обёртка ~/.local/bin)",
            needed=_disks_needed,
            check=lambda: path_exists(SUDOERS_DIR / SMARTCTL_SUDOERS_FILE) and _wrapper_path().exists(),
            apply=_sudoers_apply,
        ),
        Fixup(
            id="node-unit-smartctl-path",
            title="Поправить PATH юнита sa-home-node (~/.local/bin первым, там обёртка smartctl)",
            needed=_disks_needed,
            check=unit_check,
            apply=unit_apply,
        ),
        Fixup(
            id="journalctl-group",
            title="Добавить пользователя в группу systemd-journal",
            needed=needs.assigned("monitor"),
            check=lambda: _in_group(JOURNAL_GROUP),
            apply=_journal_apply,
        ),
    ]
