from __future__ import annotations

from sa_home_node.metrics.names import JOB_NODES, LABEL_NODE, METRIC_SCRAPE_UP
from sa_home_node.metrics.panels import number_panels, query_variable, timeseries
from sa_home_node.metrics.summary import NODE_VAR, summary_table
from sa_home_node.promexport import HOST_METRIC_PREFIX, METRIC_TEMPERATURE

NODES_DASHBOARD_UID = "sa-home-nodes"


def _host_expr(metric: str) -> str:
    return f"{HOST_METRIC_PREFIX}{metric}{{{NODE_VAR}}}"


def nodes_dashboard() -> dict:
    panels = [
        summary_table(),
        timeseries(
            "CPU загрузка, %",
            [(_host_expr("cpu_busy_pct"), "{{node}}")],
            (0, 8),
            percent=True,
        ),
        timeseries(
            "CPU steal, %",
            [(_host_expr("steal_pct"), "{{node}}")],
            (12, 8),
            percent=True,
        ),
        timeseries(
            "CPU iowait, %",
            [(_host_expr("iowait_pct"), "{{node}}")],
            (0, 16),
            percent=True,
        ),
        timeseries(
            "Load на ядро",
            [(_host_expr("load_per_core"), "{{node}}")],
            (12, 16),
        ),
        timeseries(
            "Load average",
            [
                (_host_expr("load1"), "{{node}} 1м"),
                (_host_expr("load5"), "{{node}} 5м"),
                (_host_expr("load15"), "{{node}} 15м"),
            ],
            (0, 24),
        ),
        timeseries(
            "RAM занято, %",
            [(_host_expr("mem_used_pct"), "{{node}}")],
            (12, 24),
            percent=True,
        ),
        timeseries(
            "RAM доступно, байт",
            [(_host_expr("mem_available_bytes"), "{{node}}")],
            (0, 32),
            unit="bytes",
        ),
        timeseries(
            "Swap занято, %",
            [(_host_expr("swap_used_pct"), "{{node}}")],
            (12, 32),
            percent=True,
        ),
        timeseries(
            "Диски по точкам монтирования, %",
            [(_host_expr("disk_pct"), "{{node}} {{mount}}")],
            (0, 40),
            percent=True,
        ),
        timeseries(
            "Сеть: приём, байт/с",
            [(_host_expr("net_rx_bps"), "{{node}} {{iface}}")],
            (12, 40),
            unit="Bps",
        ),
        timeseries(
            "Сеть: отдача, байт/с",
            [(_host_expr("net_tx_bps"), "{{node}} {{iface}}")],
            (0, 48),
            unit="Bps",
        ),
        timeseries(
            "Аптайм",
            [(_host_expr("uptime_s"), "{{node}}")],
            (12, 48),
            unit="s",
        ),
        timeseries(
            "Процессов",
            [(_host_expr("procs"), "{{node}}")],
            (0, 56),
        ),
        timeseries(
            "PSI (давление), %",
            [
                (_host_expr("psi_cpu"), "{{node}} cpu"),
                (_host_expr("psi_memory"), "{{node}} mem"),
                (_host_expr("psi_io"), "{{node}} io"),
            ],
            (12, 56),
            percent=True,
        ),
        timeseries(
            "conntrack, %",
            [(_host_expr("conntrack_pct"), "{{node}}")],
            (0, 64),
            percent=True,
        ),
        timeseries(
            "Ошибки NIC, в минуту",
            [(_host_expr("net_err_rate"), "{{node}}")],
            (12, 64),
        ),
        timeseries(
            "OOM-kill и стойла ядра",
            [
                (_host_expr("oom_kills"), "{{node}} oom"),
                (_host_expr("kernel_stall_events"), "{{node}} стойла"),
            ],
            (0, 72),
        ),
        timeseries(
            "Температуры, °C",
            [(f"{METRIC_TEMPERATURE}{{{NODE_VAR}}}", "{{node}} {{component}}")],
            (12, 72),
            unit="celsius",
        ),
    ]

    number_panels(panels)

    return {
        "uid": NODES_DASHBOARD_UID,
        "title": "Ноды",
        "tags": ["sa-home"],
        "timezone": "browser",
        "schemaVersion": 39,
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "templating": {
            "list": [
                query_variable(
                    "node",
                    "Нода",
                    f'label_values({METRIC_SCRAPE_UP}{{job="{JOB_NODES}"}}, {LABEL_NODE})',
                )
            ]
        },
        "panels": panels,
    }
