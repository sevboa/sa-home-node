from __future__ import annotations

import json
from pathlib import Path

import pytest

from sa_home_node.metrics import panels

FIXTURES = Path(__file__).parent.parent / "fixtures" / "metrics"
GOLDEN = json.loads((FIXTURES / "dashboards" / "panel-helpers.json").read_text(encoding="utf-8"))


def test_datasource():
    assert panels.DATASOURCE == GOLDEN["datasource"]


@pytest.mark.parametrize("case", GOLDEN["target"], ids=lambda case: case["ref"])
def test_target(case):
    result = panels.target(case["expr"], case["legend"], case["ref"], table=case["table"])
    assert result == case["result"]


def test_target_table_is_default_off():
    assert "format" not in panels.target("up", "up", "A")


@pytest.mark.parametrize("case", GOLDEN["query_variable"], ids=lambda case: case["name"])
def test_query_variable(case):
    assert panels.query_variable(case["name"], case["label"], case["query"]) == case["result"]


@pytest.mark.parametrize("case", GOLDEN["number_panels"], ids=lambda case: str(len(case["panels"])))
def test_number_panels(case):
    items = json.loads(json.dumps(case["panels"]))
    panels.number_panels(items)
    assert items == case["result"]


@pytest.mark.parametrize("case", GOLDEN["timeseries"], ids=lambda case: case["title"])
def test_timeseries(case):
    kwargs = {key: case[key] for key in ("unit", "percent") if key in case}
    series = [tuple(item) for item in case["series"]]
    result = panels.timeseries(case["title"], series, tuple(case["position"]), **kwargs)
    assert result == case["result"]


@pytest.mark.parametrize("case", GOLDEN["render_dashboard"], ids=lambda case: str(len(case["dashboard"])))
def test_render_dashboard(case):
    assert panels.render_dashboard(case["dashboard"]) == case["text"]
