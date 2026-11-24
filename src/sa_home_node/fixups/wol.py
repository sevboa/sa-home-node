from __future__ import annotations

from pathlib import Path

from sa_home_node import wol

from . import needs
from .core import Fixup, FixupContext, FixupError
from .privileged import (
    daemon_reload,
    enable_now,
    find_executable,
    install_package,
    install_root_file,
    path_exists,
)

WOL_UNIT_FILE = Path("/etc/systemd/system/sa-home-wol.service")
WOL_UNIT_NAME = WOL_UNIT_FILE.name


def wol_unit_content(ethtool_path: str, iface: str) -> str:
    return (
        "[Unit]\n"
        "Description=sa-home-bot: включить Wake-on-LAN (magic packet)\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={ethtool_path} -s {iface} wol g\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _apply() -> None:
    iface = wol.detect_local_wake_iface()
    if iface is None:
        raise FixupError("не нашёл проводной Ethernet-интерфейс по умолчанию — WoL настраивать не на чем")
    ethtool_path = find_executable("ethtool")
    if ethtool_path is None:
        install_package("ethtool", "ethtool не найден и неизвестен пакетный менеджер для установки")
        ethtool_path = find_executable("ethtool")
        if ethtool_path is None:
            raise FixupError("ethtool не нашёлся после установки")
    install_root_file(WOL_UNIT_FILE, wol_unit_content(ethtool_path, iface), mode="0644")
    daemon_reload()
    enable_now(WOL_UNIT_NAME)


def wol_fixups(ctx: FixupContext) -> list[Fixup]:
    return [
        Fixup(
            id="wol-enable",
            title="Включить Wake-on-LAN (magic packet) на проводном интерфейсе",
            needed=needs.wakeable,
            check=lambda: path_exists(WOL_UNIT_FILE),
            apply=_apply,
            platforms=("linux",),
        )
    ]
