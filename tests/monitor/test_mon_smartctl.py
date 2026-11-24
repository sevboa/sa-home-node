from __future__ import annotations

import json
import logging
import subprocess
from types import SimpleNamespace

import pytest

from sa_home_node.monitor.models import DISK_FAIL, DISK_OK, DISK_WARN
from sa_home_node.monitor.sensors import smartctl
from sa_home_node.monitor.sensors.smartctl import (
    DiskTarget,
    extract_temp,
    health_args,
    is_smart_capable,
    parse_device_spec,
    parse_health,
    parse_scan,
    read_args,
    resolve_targets,
    run_smartctl,
)
from sa_home_node.requirements import RequirementStatus, requirements_registry


def attrs(raw):
    return {"ata_smart_attributes": {"table": [{"id": i, "raw": {"value": v}} for i, v in raw.items()]}}


@pytest.fixture
def present(monkeypatch):
    monkeypatch.setattr(
        smartctl,
        "SMARTCTL_REQUIREMENT",
        SimpleNamespace(diagnose=lambda: RequirementStatus.OK, install_hint=lambda: "hint"),
    )
    reports = []
    monkeypatch.setattr(requirements_registry, "report", lambda req, st: reports.append(st))
    return reports


def fake_run(monkeypatch, *, stdout="", stderr="", code=0, exc=None):
    def run(cmd, **kwargs):
        if exc:
            raise exc
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)

    monkeypatch.setattr(smartctl.subprocess, "run", run)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("/dev/sda", DiskTarget("/dev/sda", None)),
        ("/dev/sda:sat", DiskTarget("/dev/sda", "sat")),
        ("  /dev/sda : sat  ", DiskTarget("/dev/sda", "sat")),
        ("/dev/sda:", DiskTarget("/dev/sda", None)),
        ("/dev/sdb:sat,12:sat", DiskTarget("/dev/sdb:sat,12", "sat")),
        ("", None),
        (":sat", None),
    ],
)
def test_parse_device_spec(spec, expected):
    assert parse_device_spec(spec) == expected


@pytest.mark.parametrize(
    ("name", "capable"),
    [
        ("/dev/sda", True),
        ("/dev/nvme0", True),
        ("/dev/mmcblk0", False),
        ("/dev/loop0", False),
        ("/dev/sr0", False),
        ("/dev/md0", False),
        ("/dev/dm-1", False),
        ("/dev/zram0", False),
        ("/dev/ram0", False),
    ],
)
def test_is_smart_capable(name, capable):
    assert is_smart_capable(name) is capable


def test_parse_scan():
    data = {"devices": [{"name": "/dev/sda", "type": "sat"}, {"name": "/dev/sdb", "type": ""}, {"type": "x"}]}
    assert parse_scan(data) == [DiskTarget("/dev/sda", "sat"), DiskTarget("/dev/sdb", None)]
    assert parse_scan({}) == []


def test_args():
    assert read_args(DiskTarget("/dev/sda")) == ["-j", "-A", "/dev/sda"]
    assert read_args(DiskTarget("/dev/sda", "sat")) == ["-d", "sat", "-j", "-A", "/dev/sda"]
    assert health_args(DiskTarget("/dev/sda", "sat")) == ["-d", "sat", "-j", "-H", "-A", "/dev/sda"]
    assert health_args(DiskTarget("/dev/sda")) == ["-j", "-H", "-A", "/dev/sda"]


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"temperature": {"current": 35}}, 35.0),
        ({"temperature": {"current": 35}, **attrs({194: 50})}, 35.0),
        (attrs({194: 41}), 41.0),
        ({"temperature": {}, **attrs({194: 41})}, 41.0),
        (attrs({5: 0}), None),
        ({}, None),
    ],
)
def test_extract_temp(data, expected):
    assert extract_temp(data) == expected


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({}, None),
        ({"smart_status": {}}, None),
        ({"smart_status": {"passed": False}}, DISK_FAIL),
        ({"smart_status": {"passed": True}}, DISK_OK),
        ({"smart_status": {"passed": True}, **attrs({197: 0, 198: 0})}, DISK_OK),
        ({"smart_status": {"passed": True}, **attrs({197: 3})}, DISK_WARN),
        ({"smart_status": {"passed": True}, **attrs({198: 1})}, DISK_WARN),
        ({"smart_status": {"passed": False}, **attrs({197: 0})}, DISK_FAIL),
    ],
)
def test_parse_health(data, expected):
    assert parse_health(data) == expected


def test_run_smartctl_missing_program(monkeypatch):
    reports = []
    monkeypatch.setattr(requirements_registry, "report", lambda req, st: reports.append(st))
    monkeypatch.setattr(
        smartctl,
        "SMARTCTL_REQUIREMENT",
        SimpleNamespace(diagnose=lambda: RequirementStatus.MISSING_PROGRAM, install_hint=lambda: "hint"),
    )
    called = []
    monkeypatch.setattr(smartctl.subprocess, "run", lambda *a, **k: called.append(1))
    assert run_smartctl(["-j"]) is None
    assert reports == [RequirementStatus.MISSING_PROGRAM]
    assert called == []


def test_run_smartctl_ok_with_nonzero_code(monkeypatch, present):
    payload = {"smart_status": {"passed": True}}
    fake_run(monkeypatch, stdout=json.dumps(payload), code=4)
    assert run_smartctl(["-j", "-H", "/dev/sda"]) == payload
    assert present == [RequirementStatus.OK]


def test_run_smartctl_passes_command_and_timeout(monkeypatch, present):
    seen = {}

    def run(cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        return SimpleNamespace(stdout="{}", stderr="", returncode=0)

    monkeypatch.setattr(smartctl.subprocess, "run", run)
    assert run_smartctl(["--scan", "-j"]) == {}
    assert seen["cmd"] == ["smartctl", "--scan", "-j"]
    assert seen["timeout"] == smartctl.SMARTCTL_TIMEOUT_S == 20


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({"exc": subprocess.TimeoutExpired("smartctl", 20)}, None),
        ({"exc": FileNotFoundError("smartctl")}, None),
        (
            {"stdout": "  ", "stderr": "Smartctl open device: Permission denied"},
            RequirementStatus.NEEDS_PRIVILEGE,
        ),
        ({"stdout": "", "stderr": "something else"}, None),
        ({"stdout": "{not json"}, None),
    ],
)
def test_run_smartctl_failures(monkeypatch, present, caplog, kwargs, status):
    fake_run(monkeypatch, **kwargs)
    with caplog.at_level(logging.WARNING):
        assert run_smartctl(["-j", "/dev/sda"]) is None
    assert present == ([status] if status else [])


def test_resolve_targets_specs(caplog):
    with caplog.at_level(logging.WARNING):
        result = resolve_targets(["/dev/sda:sat", "", "/dev/sdb"])
    assert result == [DiskTarget("/dev/sda", "sat"), DiskTarget("/dev/sdb", None)]
    assert "некорректная запись" in caplog.text


def test_resolve_targets_discovery(monkeypatch, present):
    scan = {
        "devices": [
            {"name": "/dev/sda", "type": "sat"},
            {"name": "/dev/mmcblk0", "type": "scsi"},
            {"name": "/dev/nvme0", "type": "nvme"},
        ]
    }
    fake_run(monkeypatch, stdout=json.dumps(scan))
    assert resolve_targets([]) == [DiskTarget("/dev/sda", "sat"), DiskTarget("/dev/nvme0", "nvme")]


def test_resolve_targets_discovery_empty(monkeypatch, present):
    fake_run(monkeypatch, stdout="")
    assert resolve_targets([]) == []
