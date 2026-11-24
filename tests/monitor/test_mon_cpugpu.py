from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from sa_home_node.monitor.sensors import cpu, gpu
from sa_home_node.requirements import RequirementStatus, requirements_registry

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def entry(current, label=""):
    return SimpleNamespace(current=current, label=label)


def ids(readings):
    return [(r.component_id, r.label, r.temperature_c) for r in readings]


def test_parse_psutil_temps():
    data = {
        "coretemp": [entry(45.5, "Package id 0"), entry(None, "x"), entry(40, "")],
        "acpitz": [entry(30, " ")],
    }
    assert ids(cpu.parse_psutil_temps(data, NOW)) == [
        ("cpu:coretemp:Package id 0", "Package id 0", 45.5),
        ("cpu:coretemp:coretemp#2", "coretemp", 40.0),
        ("cpu:acpitz:acpitz#0", "acpitz", 30.0),
    ]


def test_parse_lm_sensors():
    data = {
        "coretemp-isa-0000": {
            "Adapter": "ISA adapter",
            "Package id 0": {"temp1_input": 51.0, "temp1_max": 100.0},
            "fan": {"fan1_input": 1200},
            "empty": {"temp2_max": 1},
        }
    }
    assert ids(cpu.parse_lm_sensors(data, NOW)) == [
        ("cpu:coretemp-isa-0000:Package id 0", "Package id 0", 51.0),
        ("cpu:coretemp-isa-0000:fan", "fan", 1200.0),
    ]


def fake_psutil(monkeypatch, value=None, exc=None):
    def sensors_temperatures():
        if exc:
            raise exc
        return value

    monkeypatch.setattr("psutil.sensors_temperatures", sensors_temperatures, raising=False)


def test_read_cpu_prefers_psutil(monkeypatch):
    fake_psutil(monkeypatch, {"c": [entry(33, "p")]})
    monkeypatch.setattr(cpu.shutil, "which", lambda n: pytest.fail("sensors не нужен"))
    assert ids(cpu.read_cpu_sync(NOW)) == [("cpu:c:p", "p", 33.0)]


@pytest.mark.parametrize("value", [{}, {"c": [entry(None)]}])
def test_read_cpu_falls_back_to_sensors(monkeypatch, value):
    fake_psutil(monkeypatch, value)
    monkeypatch.setattr(cpu.shutil, "which", lambda n: "/bin/sensors")
    out = json.dumps({"chip": {"f": {"temp1_input": 44}}})
    monkeypatch.setattr(
        cpu.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=out)
    )
    assert ids(cpu.read_cpu_sync(NOW)) == [("cpu:chip:f", "f", 44.0)]


def test_read_cpu_psutil_error_and_no_sensors(monkeypatch):
    fake_psutil(monkeypatch, exc=OSError("x"))
    monkeypatch.setattr(cpu.shutil, "which", lambda n: None)
    assert cpu.read_cpu_sync(NOW) == []


def test_read_cpu_sensors_failure(monkeypatch):
    fake_psutil(monkeypatch, None)
    monkeypatch.setattr(cpu.shutil, "which", lambda n: "/bin/sensors")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("sensors", 10)

    monkeypatch.setattr(cpu.subprocess, "run", boom)
    assert cpu.read_cpu_sync(NOW) == []


def test_parse_nvidia_smi_csv():
    out = "0, Tesla V100, 41\n1, Card B, [N/A]\n, no index, 40\nshort,row\n2, , 55\n"
    assert ids(gpu.parse_nvidia_smi_csv(out, NOW)) == [
        ("gpu:0", "Tesla V100", 41.0),
        ("gpu:2", "GPU2", 55.0),
    ]


@pytest.fixture
def registry():
    requirements_registry.reset()
    yield requirements_registry
    requirements_registry.reset()


def test_read_gpu_without_nvidia_smi(monkeypatch, registry):
    monkeypatch.setattr("shutil.which", lambda n: None)
    monkeypatch.setattr(
        gpu.subprocess, "run", lambda *a, **k: pytest.fail("не должен вызываться")
    )
    assert gpu.read_gpu_sync(NOW) == []
    assert (
        registry.status_for(gpu.NVIDIA_SMI_REQUIREMENT)
        is RequirementStatus.MISSING_PROGRAM
    )
    assert registry.problem_for(gpu.NVIDIA_SMI_REQUIREMENT)["id"] == "nvidia-smi"


def test_read_gpu_ok_and_failure(monkeypatch, registry):
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/nvidia-smi")
    calls = []

    def ok(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(stdout="0, GPU, 60\n")

    monkeypatch.setattr(gpu.subprocess, "run", ok)
    assert ids(gpu.read_gpu_sync(NOW)) == [("gpu:0", "GPU", 60.0)]
    assert calls[0][0] == "nvidia-smi"
    assert registry.problem_for(gpu.NVIDIA_SMI_REQUIREMENT) is None

    def boom(*a, **k):
        raise subprocess.CalledProcessError(9, "nvidia-smi")

    monkeypatch.setattr(gpu.subprocess, "run", boom)
    assert gpu.read_gpu_sync(NOW) == []
