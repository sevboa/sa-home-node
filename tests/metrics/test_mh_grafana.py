from __future__ import annotations

import json
from pathlib import Path

import pytest

from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.metrics.grafana import grafana_ini_values, set_ini_values

FIXTURES = Path(__file__).parent.parent / "fixtures" / "metrics"
GOLDEN = json.loads((FIXTURES / "fixups" / "grafana-ini.json").read_text(encoding="utf-8"))
VALUES = GOLDEN["values"]


def test_ini_values():
    config = MetricsConfig(**GOLDEN["config"])
    assert grafana_ini_values(config, GOLDEN["host"]) == VALUES


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda case: case["name"])
def test_set_ini_values(case):
    assert set_ini_values(case["before"], VALUES) == case["after"]


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda case: case["name"])
def test_set_ini_values_is_idempotent(case):
    assert set_ini_values(case["after"], VALUES) == case["after"]
