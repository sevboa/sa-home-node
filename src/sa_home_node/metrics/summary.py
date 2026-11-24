from __future__ import annotations

from sa_home_node.metrics.names import JOB_NODES, LABEL_NODE, METRIC_SCRAPE_UP
from sa_home_node.metrics.panels import DATASOURCE, target
from sa_home_node.promexport import HOST_METRIC_PREFIX, METRIC_TEMPERATURE

NODE_VAR = f'{LABEL_NODE}=~"$node"'


def summary_table() -> dict:
    return {
        "type": "table",
        "title": "Сводка по нодам",
        "datasource": DATASOURCE,
        "gridPos": {"x": 0, "y": 0, "w": 24, "h": 8},
        "fieldConfig": {
            "defaults": {},
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "CPU %"},
                    "properties": [{"id": "unit", "value": "percent"}],
                },
                {
                    "matcher": {"id": "byName", "options": "RAM занято %"},
                    "properties": [{"id": "unit", "value": "percent"}],
                },
                {
                    "matcher": {"id": "byName", "options": "диск макс %"},
                    "properties": [{"id": "unit", "value": "percent"}],
                },
                {
                    "matcher": {"id": "byName", "options": "steal %"},
                    "properties": [{"id": "unit", "value": "percent"}],
                },
                {
                    "matcher": {"id": "byName", "options": "аптайм"},
                    "properties": [{"id": "unit", "value": "s"}],
                },
                {
                    "matcher": {"id": "byName", "options": "t макс, °C"},
                    "properties": [{"id": "unit", "value": "celsius"}],
                },
            ],
        },
        "targets": [
            target(f'{METRIC_SCRAPE_UP}{{job="{JOB_NODES}",{NODE_VAR}}}', "", "A", table=True),
            target(f"{HOST_METRIC_PREFIX}cpu_busy_pct{{{NODE_VAR}}}", "", "B", table=True),
            target(f"{HOST_METRIC_PREFIX}mem_used_pct{{{NODE_VAR}}}", "", "C", table=True),
            target(
                f"max by ({LABEL_NODE}) ({HOST_METRIC_PREFIX}disk_pct{{{NODE_VAR}}})", "", "D", table=True
            ),
            target(f"{HOST_METRIC_PREFIX}steal_pct{{{NODE_VAR}}}", "", "E", table=True),
            target(f"{HOST_METRIC_PREFIX}uptime_s{{{NODE_VAR}}}", "", "F", table=True),
            target(f"max by ({LABEL_NODE}) ({METRIC_TEMPERATURE}{{{NODE_VAR}}})", "", "G", table=True),
        ],
        "transformations": [
            {"id": "merge", "options": {}},
            {
                "id": "organize",
                "options": {
                    "excludeByName": {"Time": True, "job": True, "instance": True},
                    "renameByName": {
                        LABEL_NODE: "Нода",
                        "Value #A": "up",
                        "Value #B": "CPU %",
                        "Value #C": "RAM занято %",
                        "Value #D": "диск макс %",
                        "Value #E": "steal %",
                        "Value #F": "аптайм",
                        "Value #G": "t макс, °C",
                    },
                },
            },
        ],
    }
