from __future__ import annotations

from pathlib import Path

from sa_home_node.fixups import privileged


class SudoIO:
    def read_root(self, path: Path) -> str | None:
        return privileged.read_text_if_exists(path)

    def write_root(self, path: Path, text: str) -> None:
        with privileged.temp_file(text, ".restore") as tmp:
            privileged.install_from(tmp, path, mode="0600", make_parents=True)

    def unit_active(self, unit: str, *, user: bool = False) -> bool:
        return privileged.unit_is_active(unit, user=user)

    def restart(self, unit: str, *, user: bool = False) -> None:
        if user:
            privileged.run_user(["systemctl", "--user", "restart", unit])
        else:
            privileged.run_sudo(["systemctl", "restart", unit])
