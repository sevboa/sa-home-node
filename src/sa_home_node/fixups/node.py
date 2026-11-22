from __future__ import annotations

import getpass
from pathlib import Path

from sa_home_node.fixups.core import Fixup, FixupContext, FixupError
from sa_home_node.fixups.privileged import find_executable, path_exists, run_sudo, run_user
from sa_home_node.fixups.unit import exec_command, with_exec_command

LINGER_DIR = Path("/var/lib/systemd/linger")
NODE_EXECUTABLE = "sa-home-node"
RUN_COMMAND = "run"


def _is_new_form(argv: list[str]) -> bool:
    name = argv[0].replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe")
    return len(argv) > 1 and argv[1] == RUN_COMMAND and name == NODE_EXECUTABLE


def _config_arg(argv: list[str]) -> str | None:
    if argv[1:3] != ["--service", "node"] or "--config" not in argv[3:-1]:
        return None
    return argv[argv.index("--config") + 1]


def _node_executable(old: str, windows: bool) -> str:
    name = NODE_EXECUTABLE + (".exe" if windows else "")
    cut = max(old.rfind("/"), old.rfind("\\")) + 1
    sibling = old[:cut] + name
    if cut and Path(sibling).is_file():
        return sibling
    found = find_executable(name)
    if found is None:
        raise FixupError(
            "не найден sa-home-node рядом со старой командой и в PATH — сначала установите sa-home-node"
        )
    return found


def node_fixups(ctx: FixupContext) -> list[Fixup]:
    unit = ctx.host.node_unit(ctx.config.path)
    user = getpass.getuser()

    def unclear() -> FixupError:
        return FixupError(f"непонятная строка запуска в {unit.path} — правьте руками")

    def read() -> str:
        try:
            return unit.path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise unclear() from exc

    def check() -> bool:
        if not unit.path.exists():
            return True
        argv = exec_command(read(), unit.format)
        return argv is not None and _is_new_form(argv)

    def apply() -> None:
        if check():
            return
        text = read()
        argv = exec_command(text, unit.format)
        config = _config_arg(argv) if argv else None
        if config is None:
            raise unclear()
        command = [_node_executable(argv[0], ctx.host.name == "windows"), RUN_COMMAND, "--config", config]
        text = with_exec_command(text, unit.format, command)
        if text is None:
            raise unclear()
        try:
            unit.path.write_bytes(text.encode("utf-8"))
        except PermissionError as exc:
            raise FixupError(f"нужны права администратора для записи {unit.path}") from exc
        if unit.reload_argv is not None:
            run_user(unit.reload_argv)

    return [
        Fixup(
            id="node-linger",
            title="Включить linger (нода живёт без входа в сессию)",
            check=lambda: path_exists(LINGER_DIR / user),
            apply=lambda: run_sudo(["loginctl", "enable-linger", user]),
            needed=lambda _: unit.format == "systemd-user" and unit.path.exists(),
        ),
        Fixup(
            id="node-unit-command",
            title="Привести строку запуска юнита ноды к sa-home-node run",
            check=check,
            apply=apply,
            platforms=("linux", "windows"),
        ),
    ]
