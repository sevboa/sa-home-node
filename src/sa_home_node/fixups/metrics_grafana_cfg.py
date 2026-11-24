from __future__ import annotations

from pathlib import Path

from sa_home_node import promexport
from sa_home_node.fixups import needs
from sa_home_node.fixups.core import Fixup, FixupContext, FixupError
from sa_home_node.fixups.privileged import (
    daemon_reload,
    enable_now,
    file_matches,
    install_root_file,
    path_exists,
    read_text,
    run_sudo,
    temp_file,
    unit_is_active,
)
from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.metrics.grafana import grafana_ini_values, set_ini_values
from sa_home_node.metrics.unitdropin import DROPIN_NAME, overlay_dropin_content

GRAFANA_INI = Path("/etc/grafana/grafana.ini")
GRAFANA_DROPIN_DIR = Path("/etc/systemd/system/grafana-server.service.d")
GRAFANA_DROPIN = GRAFANA_DROPIN_DIR / DROPIN_NAME
GRAFANA_SERVICE = "grafana-server.service"


def grafana_config_fixup(ctx: FixupContext) -> Fixup:
    config = ctx.section("metrics", MetricsConfig)
    dropin = overlay_dropin_content(config.overlay_unit)

    def ini_texts() -> tuple[str, str]:
        address = promexport.overlay_address()
        if address is None:
            raise FixupError("у ноды нет адреса оверлейной сети — поднимите оверлейную сеть")
        current = read_text(GRAFANA_INI)
        return current, set_ini_values(current, grafana_ini_values(config, address))

    def check() -> bool:
        if not path_exists(GRAFANA_INI):
            return False
        current, wanted = ini_texts()
        return current == wanted and file_matches(GRAFANA_DROPIN, dropin) and unit_is_active(GRAFANA_SERVICE)

    def apply() -> None:
        if not path_exists(GRAFANA_INI):
            raise FixupError("/etc/grafana/grafana.ini нет — сначала install-grafana")
        _, wanted = ini_texts()
        with temp_file(wanted) as tmp:
            run_sudo(
                ["install", "-D", "-m", "0640", "-o", "root", "-g", "grafana", str(tmp), str(GRAFANA_INI)]
            )
        install_root_file(GRAFANA_DROPIN, dropin, mode="0644", make_parents=True)

        daemon_reload()
        enable_now(GRAFANA_SERVICE)
        run_sudo(["systemctl", "restart", GRAFANA_SERVICE])

    return Fixup(
        id="grafana-config",
        title="Настроить grafana (слушает только оверлейный адрес, анонимный вход выключен)",
        check=check,
        apply=apply,
        needed=needs.metrics_hub,
    )
