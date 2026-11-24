from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sa_home_node.monitor.models import BaselineStats, SensorReading
from sa_home_node.monitor.policy import BaselinePolicy, FixedThresholdPolicy

FIXTURE = Path(__file__).parents[1] / "fixtures" / "monitor" / "domain" / "policy-bands.json"


def _reading(temp: float) -> SensorReading:
    return SensorReading("cpu:package", "cpu", "CPU", temp, datetime(2026, 1, 1, tzinfo=UTC))


def _cases():
    out = []
    for case in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]:
        if case["policy"] == "fixed":
            out.append((FixedThresholdPolicy(**case["params"]), case["bands"], None))
        else:
            for sub in case["cases"]:
                policy = BaselinePolicy(**case["params"], stats=BaselineStats(**sub["stats"]))
                out.append((policy, sub["bands"], sub["effective_warn_c"]))
    return out


@pytest.mark.parametrize("policy,bands,effective", _cases())
def test_bands_match_reference(policy, bands, effective):
    for item in bands:
        assert policy.band(_reading(item["temp"])) == item["band"], item
    if effective is not None:
        assert policy._effective_warn() == pytest.approx(effective)


@pytest.mark.parametrize(
    "count,mean,std,expected",
    [(29, 40.0, 1.0, 80.0), (30, 40.0, 1.0, 52.0), (30, 40.0, 10.0, 80.0), (30, 90.0, 5.0, 80.0)],
)
def test_effective_warn(count, mean, std, expected):
    policy = BaselinePolicy(80.0, 90.0, 5.0, BaselineStats(count, mean, std), 30, 4.0, 3.0)
    assert policy._effective_warn() == pytest.approx(expected)
