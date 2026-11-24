from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sa_home_node import host
from sa_home_node.monitor.config import SensorsConfig
from sa_home_node.monitor.hostmodels import HostMetricReading
from sa_home_node.monitor.models import DiskSummary, SensorReading
from sa_home_node.monitor.sensors import cpu, disks, gpu, hostscan
from sa_home_node.monitor.sensors import source as source_module
from sa_home_node.monitor.sensors.source import SensorSource

FIXTURE = Path(__file__).parents[1] / "fixtures" / "monitor" / "sensors" / "host-source.json"
SCENARIOS = json.loads(FIXTURE.read_text(encoding="utf-8"))
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
SINCE = datetime(2026, 1, 15, 11, 55, tzinfo=UTC)


def reading(kind: str) -> SensorReading:
    return SensorReading(f"{kind}:0", kind, kind, 1.0, NOW)


class FakeProvider:
    def __init__(self, platform: str, problem: dict | None = None):
        self.platform = platform
        self._problem = problem

    def read_cpu(self, now):
        return [reading("prov-cpu")]

    def read_disks(self, now):
        return [reading("prov-disk")]

    def read_disk_summaries(self):
        return [DiskSummary("prov", None, None, None, None)]

    def problem(self):
        return self._problem


@pytest.fixture
def native(monkeypatch):
    calls: list[str] = []

    def make(name):
        def fake(*args):
            calls.append(name)
            return [reading(name)]

        return fake

    monkeypatch.setattr(source_module, "utc_now", lambda: NOW)
    monkeypatch.setattr(cpu, "read_cpu_sync", make("cpu"))
    monkeypatch.setattr(gpu, "read_gpu_sync", make("gpu"))
    monkeypatch.setattr(disks, "read_disks_sync", make("disk"))
    monkeypatch.setattr(
        disks,
        "read_disk_summaries_sync",
        lambda *a: calls.append("summary") or [DiskSummary("native", None, None, None, None)],
    )
    return calls


GPU_ON = SensorsConfig.model_validate({"gpu": {"enabled": True}})


def kinds(readings):
    return [r.kind for r in readings]


def test_provider_of_host_platform_is_used(native):
    src = SensorSource(GPU_ON, [FakeProvider(host.current().name)])
    assert kinds(asyncio.run(src.read_cpu())) == ["prov-cpu"]
    assert kinds(asyncio.run(src.read_disks())) == ["prov-disk"]
    assert asyncio.run(src.read_disk_summaries({}))[0].label == "prov"
    assert kinds(asyncio.run(src.read_gpu())) == ["gpu"]
    assert native == ["gpu"]


def test_foreign_platform_uses_native(native):
    src = SensorSource(SensorsConfig(), [FakeProvider("other")])
    assert kinds(asyncio.run(src.read_cpu())) == ["cpu"]
    assert kinds(asyncio.run(src.read_disks())) == ["disk"]
    assert asyncio.run(src.read_disk_summaries({}))[0].label == "native"


def test_windows_without_provider_is_empty(native, monkeypatch):
    monkeypatch.setattr(host, "current", lambda: host.select("win32"))
    src = SensorSource(GPU_ON)
    assert asyncio.run(src.read_cpu()) == []
    assert asyncio.run(src.read_disks()) == []
    assert asyncio.run(src.read_disk_summaries({})) == []
    assert kinds(asyncio.run(src.read_gpu())) == ["gpu"]


def test_disabled_sensors_do_not_call(native):
    config = SensorsConfig.model_validate(
        {"cpu": {"enabled": False}, "gpu": {"enabled": False}, "disks": {"enabled": False}}
    )
    src = SensorSource(config, [FakeProvider(host.current().name)])
    assert asyncio.run(src.read_all()) == []
    assert asyncio.run(src.read_smart_snapshots()) == []
    assert native == []


def test_read_all_order(native):
    assert kinds(asyncio.run(SensorSource(GPU_ON).read_all())) == ["cpu", "gpu", "disk"]


def test_problems_skip_empty():
    providers = [FakeProvider("a", {"x": 1}), FakeProvider("b"), FakeProvider("c", {"y": 2})]
    assert SensorSource(SensorsConfig(), providers).problems() == [{"x": 1}, {"y": 2}]


@pytest.mark.parametrize("name", [n for n in SCENARIOS if n != "default_args_disabled"])
def test_read_host_matches_fixture(monkeypatch, name):
    enabled, export = (part == "true" for part in name.split("_")[1::2])
    calls: list[dict] = []

    def fake(now, window, kernel_since):
        calls.append({"now": now.isoformat(), "window": window, "kernel_since": kernel_since.isoformat()})
        return [HostMetricReading("host:load1", "load1", "load 1 мин", 0.5, "", now)]

    monkeypatch.setattr(source_module, "utc_now", lambda: NOW)
    monkeypatch.setattr(hostscan, "read_host_sync", fake)
    config = SensorsConfig.model_validate({"host": {"enabled": enabled, "sample_window_s": 5.0}})
    result = asyncio.run(SensorSource(config).read_host(SINCE, export))
    expected = SCENARIOS[name]
    assert [{**asdict(r), "taken_at": r.taken_at.isoformat()} for r in result] == expected["readings"]
    assert calls == expected["sync_calls"]


def test_read_host_default_args_disabled(monkeypatch):
    monkeypatch.setattr(hostscan, "read_host_sync", lambda *a: pytest.fail("called"))
    config = SensorsConfig.model_validate({"host": {"enabled": False}})
    expected = SCENARIOS["default_args_disabled"]
    assert asyncio.run(SensorSource(config).read_host()) == expected["readings"]
