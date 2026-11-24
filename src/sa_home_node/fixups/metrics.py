from __future__ import annotations

from sa_home_node.fixups.core import Fixup, FixupContext
from sa_home_node.fixups.metrics_grafana_cfg import grafana_config_fixup
from sa_home_node.fixups.metrics_grafana_pkg import grafana_pkg_fixups
from sa_home_node.fixups.metrics_provision import grafana_provisioning_fixup
from sa_home_node.fixups.metrics_vm import vm_fixups
from sa_home_node.fixups.needs import metrics_hub


def metrics_fixups(ctx: FixupContext) -> list[Fixup]:
    if not metrics_hub(ctx):
        return []
    return [
        *vm_fixups(ctx),
        *grafana_pkg_fixups(),
        grafana_config_fixup(ctx),
        grafana_provisioning_fixup(ctx),
    ]
