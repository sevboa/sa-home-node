from __future__ import annotations

from getpass import getuser
from pathlib import Path

from . import needs
from .core import Fixup, FixupContext
from .privileged import install_package, install_root_file, path_exists, run_sudo

POLKIT_RULE_FILE = Path("/etc/polkit-1/rules.d/50-sa-home-node-power.rules")
POLKIT_PACKAGE = "polkitd"
POLKIT_ACTIONS = (
    "power-off",
    "power-off-multiple-sessions",
    "reboot",
    "reboot-multiple-sessions",
    "suspend",
    "suspend-multiple-sessions",
)


def polkit_rule_content(user: str) -> str:
    ids = ",\n        ".join(f'"org.freedesktop.login1.{a}"' for a in POLKIT_ACTIONS)
    return (
        "// Сгенерировано sa-home-bot (nodectl fix) — нода-супервизор "
        "выключает/перезагружает/усыпляет свою workstation без sudo.\n"
        "polkit.addRule(function(action, subject) {\n"
        f"    if ([\n        {ids}\n    ].indexOf(action.id) !== -1 "
        f'&& subject.user == "{user}") {{\n'
        "        return polkit.Result.YES;\n"
        "    }\n"
        "});\n"
    )


def _apply() -> None:
    if not POLKIT_RULE_FILE.parent.is_dir():
        install_package(
            POLKIT_PACKAGE,
            f"polkit не установлен, и неизвестен пакетный менеджер для установки пакета {POLKIT_PACKAGE!r}",
        )
    run_sudo(["install", "-d", "-m", "0755", str(POLKIT_RULE_FILE.parent)])
    install_root_file(POLKIT_RULE_FILE, polkit_rule_content(getuser()), mode="0644")


def power_fixups(ctx: FixupContext) -> list[Fixup]:
    return [
        Fixup(
            id="power-control-polkit",
            title="Разрешить ноде выключать/перезагружать/усыплять машину без sudo (polkit)",
            needed=needs.power_controllable,
            check=lambda: path_exists(POLKIT_RULE_FILE),
            apply=_apply,
        )
    ]
