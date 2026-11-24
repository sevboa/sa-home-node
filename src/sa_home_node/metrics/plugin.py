from __future__ import annotations

from sa_home_node.fixups.metrics import metrics_fixups
from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.metrics.hub import run_hub
from sa_home_node.plugins import Plugin, ServiceSpec

PLUGIN = Plugin(
    name="metrics",
    config_model=MetricsConfig,
    fixups=metrics_fixups,
    services=(
        ServiceSpec(
            "metrics-hub",
            run_hub,
            process=False,
            assignable=False,
            auto=lambda config: config.section("metrics", MetricsConfig).hub,
        ),
    ),
)
