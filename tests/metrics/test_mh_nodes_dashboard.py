from __future__ import annotations

import json
import re
from pathlib import Path

from sa_home_node import promexport
from sa_home_node.metrics.nodes_dashboard import nodes_dashboard
from sa_home_node.metrics.panels import render_dashboard
from sa_home_node.monitor.metrics import GAUGES, METRICS

FIXTURES = Path(__file__).parent.parent / "fixtures" / "metrics"
EXPECTED = (FIXTURES / "dashboards" / "nodes.json").read_text(encoding="utf-8")


def test_nodes_dashboard_matches_fixture_bytes():
    assert render_dashboard(nodes_dashboard()) == EXPECTED


def test_expressions_use_exported_metrics():
    expressions = [target["expr"] for panel in nodes_dashboard()["panels"] for target in panel["targets"]]
    pattern = re.compile(rf"{promexport.HOST_METRIC_PREFIX}([a-z0-9_]+)")
    used = {name for expr in expressions for name in pattern.findall(expr)}
    assert used
    assert used <= set(METRICS) | set(GAUGES)
    assert any(promexport.METRIC_TEMPERATURE in expr for expr in expressions)


def test_panels_are_numbered_from_one():
    ids = [panel["id"] for panel in json.loads(EXPECTED)["panels"]]
    assert [panel["id"] for panel in nodes_dashboard()["panels"]] == ids == list(range(1, len(ids) + 1))
