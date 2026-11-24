from __future__ import annotations

import pytest
from pydantic import ValidationError

import sa_home_node.monitor.config as monitor_config
from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.monitor.plugin import PLUGIN as MONITOR

DEFAULTS = {
    "export": False,
    "hub": False,
    "exporter_port": 9100,
    "data_dir": "/mnt/data/metrics",
    "runtime_dir": "/var/lib/sa-home-metrics",
    "retention": "1y",
    "scrape_interval": "30s",
    "grafana_port": 3000,
    "vm_port": 8428,
    "grafana_public_url": "",
    "overlay_unit": "",
}


def test_defaults() -> None:
    assert MetricsConfig().model_dump() == DEFAULTS


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"export": True, "exporter_port": 9200}, {"export": True, "exporter_port": 9200}),
        (
            {"hub": True, "runtime_dir": "/srv/m", "data_dir": "/srv/d"},
            {"hub": True, "runtime_dir": "/srv/m", "data_dir": "/srv/d"},
        ),
        ({"retention": "30d", "scrape_interval": "15s"}, {"retention": "30d", "scrape_interval": "15s"}),
        ({"grafana_port": 3001, "vm_port": 8429}, {"grafana_port": 3001, "vm_port": 8429}),
        (
            {"grafana_public_url": "https://g.example", "overlay_unit": "overlay.service"},
            {"grafana_public_url": "https://g.example", "overlay_unit": "overlay.service"},
        ),
    ],
)
def test_keys_map_to_fields(raw: dict, expected: dict) -> None:
    assert MetricsConfig(**raw).model_dump() == {**DEFAULTS, **expected}


@pytest.mark.parametrize("key", ["exporter_port", "grafana_port", "vm_port"])
@pytest.mark.parametrize("port", [0, 65536])
def test_port_bounds(key: str, port: int) -> None:
    with pytest.raises(ValidationError):
        MetricsConfig(**{key: port})


@pytest.mark.parametrize("key", ["exporter_port", "grafana_port", "vm_port"])
@pytest.mark.parametrize("port", [1, 65535])
def test_port_edges_ok(key: str, port: int) -> None:
    assert getattr(MetricsConfig(**{key: port}), key) == port


def test_unknown_key_kept_in_extra() -> None:
    assert MetricsConfig(vpn_port=9101).model_extra == {"vpn_port": 9101}


def test_one_place() -> None:
    assert dict(MONITOR.sections)["metrics"] is MetricsConfig
    assert not hasattr(monitor_config, "MetricsConfig")
