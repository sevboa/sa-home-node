from sa_home_node.monitor.config import MetricsConfig, MonitorConfig, ScheduleConfig, SensorsConfig
from sa_home_node.monitor.run import make_handler, run_monitor
from sa_home_node.plugins import Plugin, ServiceSpec

PLUGIN = Plugin(
    name="monitor",
    config_model=MonitorConfig,
    sections=(("sensors", SensorsConfig), ("schedule", ScheduleConfig), ("metrics", MetricsConfig)),
    services=(
        ServiceSpec("monitor", run_monitor, handler=make_handler, process=True, endpoint=lambda c: c.socket),
    ),
)
