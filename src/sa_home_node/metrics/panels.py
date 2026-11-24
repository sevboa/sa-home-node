from __future__ import annotations

import json
from string import ascii_uppercase

from sa_home_node.metrics.names import DATASOURCE_TYPE, DATASOURCE_UID

DATASOURCE = {"type": DATASOURCE_TYPE, "uid": DATASOURCE_UID}


def target(expr: str, legend: str, ref: str, *, table: bool = False) -> dict:
    result = {
        "datasource": DATASOURCE,
        "expr": expr,
        "legendFormat": legend,
        "refId": ref,
    }
    if table:
        result["format"] = "table"
        result["instant"] = True
    return result


def query_variable(name: str, label: str, query: str) -> dict:
    return {
        "name": name,
        "label": label,
        "type": "query",
        "datasource": DATASOURCE,
        "query": {"query": query, "refId": name},
        "refresh": 2,
        "multi": True,
        "includeAll": True,
        "current": {"selected": True, "text": "All", "value": "$__all"},
    }


def number_panels(panels: list[dict]) -> None:
    for i, panel in enumerate(panels, start=1):
        panel["id"] = i


def timeseries(
    title: str,
    series: list[tuple[str, str]],
    position: tuple[int, int],
    *,
    unit: str = "short",
    percent: bool = False,
) -> dict:
    x, y = position
    defaults = {"unit": "percent" if percent else unit}
    if percent:
        defaults["min"] = 0
        defaults["max"] = 100
    targets = [target(expr, legend, ascii_uppercase[i]) for i, (expr, legend) in enumerate(series)]
    return {
        "type": "timeseries",
        "title": title,
        "datasource": DATASOURCE,
        "gridPos": {"x": x, "y": y, "w": 12, "h": 8},
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "targets": targets,
    }


def render_dashboard(dashboard: dict) -> str:
    return json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n"
