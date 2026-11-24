from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sa_home_node.monitor.hostmodels import (
    INSTANCE_LABEL_IFACE,
    INSTANCE_LABEL_MOUNT,
    HostMetricPolicy,
    HostMetricReading,
    HostMetricSpec,
)
from sa_home_node.monitor.metrics import (
    GAUGES,
    METRICS,
    compute_host_diff,
    instance_of,
    metric_spec,
)
from sa_home_node.monitor.models import OK

FIXTURE = Path(__file__).parents[1] / "fixtures" / "monitor" / "domain" / "host-gauges.json"
METRICS_FIXTURE = Path(__file__).parents[1] / "fixtures" / "monitor" / "domain" / "metrics.json"

NOW = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
VALUES = (-1.0, 0.0, 0.5, 50.0, 100.0, 1000000000.0)


def _spec_of(row: dict) -> HostMetricSpec:
    return HostMetricSpec(
        metric=row["metric"],
        label=row["label"],
        unit=row["unit"],
        direction=row["direction"],
        warn=row["warn"],
        crit=row["crit"],
        hysteresis=row["hysteresis"],
        hint=row["hint"],
        instance_label=row["instance_label"],
    )


def _resolver(alert: int = 0, clear: int = 0, spec: HostMetricSpec | None = None):
    return lambda r: HostMetricPolicy(spec or metric_spec(r.metric), alert, clear)


def _reading(metric: str, value: float, component_id: str | None = None) -> HostMetricReading:
    spec = metric_spec(metric)
    return HostMetricReading(component_id or f"host:{metric}", metric, spec.label, value, spec.unit, NOW)


def test_constants() -> None:
    assert json.loads(FIXTURE.read_text(encoding="utf-8"))["constants"] == {
        "INSTANCE_LABEL_MOUNT": INSTANCE_LABEL_MOUNT,
        "INSTANCE_LABEL_IFACE": INSTANCE_LABEL_IFACE,
    }


@pytest.mark.parametrize("name", list(GAUGES))
def test_gauge_specs(name: str) -> None:
    row = json.loads(FIXTURE.read_text(encoding="utf-8"))["gauges"][name]
    spec = GAUGES[name]
    assert _spec_of(
        {
            "metric": spec.metric,
            "label": spec.label,
            "unit": spec.unit,
            "direction": spec.direction,
            "warn": spec.warn,
            "crit": spec.crit,
            "hysteresis": spec.hysteresis,
            "hint": spec.hint,
            "instance_label": spec.instance_label,
        }
    ) == _spec_of(row)
    assert (spec.warn, spec.crit) == (None, None)


def test_gauge_keys() -> None:
    reference = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert list(GAUGES) == reference["gauge_keys"]
    assert not set(reference["gauge_keys"]) & set(reference["metric_keys_disjoint_from_gauges"])
    assert len(GAUGES) == 11


def test_metric_spec_first_metrics_then_gauges() -> None:
    reference = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for name, row in reference["metric_spec"].items():
        if name in ("nope", "", "host:steal_pct"):
            assert metric_spec(name) is None
        else:
            assert metric_spec(name) == _spec_of(row)


@pytest.mark.parametrize(
    ("component_id", "metric", "instance"),
    [
        ("host:disk_pct:/mnt/data", "disk_pct", "/mnt/data"),
        ("host:disk_pct:/", "disk_pct", "/"),
        ("host:disk_pct:/mnt/my disk", "disk_pct", "/mnt/my disk"),
        ("host:disk_pct:", "disk_pct", ""),
        ("host:disk_pct", "disk_pct", None),
        ("host:net_rx_bps:eth0", "net_rx_bps", "eth0"),
        ("host:net_rx_bps:eth0", "net_tx_bps", None),
        ("host:load1", "load1", None),
        ("host:steal_pct", "steal_pct", None),
        ("host:a:b:c", "a", "b:c"),
        ("disk_pct:/", "disk_pct", None),
        ("", "", None),
    ],
)
def test_instance_of(component_id: str, metric: str, instance: str | None) -> None:
    assert instance_of(component_id, metric) == instance


@pytest.mark.parametrize("name", list(GAUGES))
def test_gauge_bands(name: str) -> None:
    reference = json.loads(FIXTURE.read_text(encoding="utf-8"))
    bands = reference["gauge_bands"][name]
    for item in bands:
        assert GAUGES[name].band(item["value"]) == item["band"], (name, item)


@pytest.mark.parametrize("name", ["steal_pct", "mem_available_pct", "load_per_core"])
def test_band_without_thresholds(name: str) -> None:
    reference = json.loads(FIXTURE.read_text(encoding="utf-8"))
    case = reference["band_without_thresholds"][name]
    spec = METRICS[name].without_thresholds()
    assert spec == _spec_of(case["spec"])
    for item in case["bands"]:
        assert spec.band(item["value"]) == item["band"], item


@pytest.mark.parametrize("name", list(METRICS))
def test_without_thresholds_all(name: str) -> None:
    reference = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert METRICS[name].without_thresholds() == _spec_of(reference["without_thresholds_all"][name])


def test_without_then_with_thresholds() -> None:
    reference = json.loads(FIXTURE.read_text(encoding="utf-8"))
    spec = METRICS["steal_pct"].without_thresholds().with_thresholds(7.0, 20.0)
    assert spec == _spec_of(reference["without_then_with_thresholds"])


@pytest.mark.parametrize("name", ["disk_pct", "net_rx_bps"])
def test_with_thresholds_keeps_instance_label(name: str) -> None:
    reference = json.loads(FIXTURE.read_text(encoding="utf-8"))
    spec = GAUGES[name].with_thresholds(1.0, 2.0)
    if name == "disk_pct":
        assert spec == _spec_of(reference["with_thresholds_keeps_instance_label"])
    assert spec.instance_label == {"disk_pct": INSTANCE_LABEL_MOUNT, "net_rx_bps": INSTANCE_LABEL_IFACE}[name]


@pytest.mark.parametrize("name", ["cpu_busy_pct", "disk_pct"])
def test_gauge_diff(name: str) -> None:
    reference = json.loads(FIXTURE.read_text(encoding="utf-8"))
    case = next(c for c in reference["gauge_diff"] if c["metric"] == name)
    component_id = case["component_id"]
    for step in case["steps"]:
        diff = compute_host_diff([_reading(name, step["value"], component_id)], {}, _resolver(), NOW)
        state = diff.states[0]
        assert (state.component_id, state.metric, state.label, state.value, state.unit) == (
            component_id,
            name,
            step["diff"]["states"][0]["label"],
            step["value"],
            step["diff"]["states"][0]["unit"],
        )
        assert (state.status, state.consecutive_count, state.alerting_since) == (
            step["diff"]["states"][0]["status"],
            step["diff"]["states"][0]["consecutive_count"],
            step["diff"]["states"][0]["alerting_since"],
        )
        assert diff.transitions == []


@pytest.mark.parametrize("value", [1.0, 50.0, 99.0, 0.0])
def test_stripped_threshold_diff(value: float) -> None:
    spec = METRICS["steal_pct"].without_thresholds()
    diff = compute_host_diff([_reading("steal_pct", value)], {}, _resolver(spec=spec), NOW)
    state = diff.states[0]
    assert (state.status, state.consecutive_count, state.alerting_since) == (OK, 0, None)
    assert diff.transitions == []


@pytest.mark.parametrize("name", list(METRICS))
def test_metrics_instance_label(name: str) -> None:
    assert METRICS[name].instance_label is None
