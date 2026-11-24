from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from sa_home_node import requirements
from sa_home_node.monitor.models import POWER_CLEAN, POWER_UNEXPECTED
from sa_home_node.monitor.sensors import power
from sa_home_node.requirements import RequirementStatus, requirements_registry

LAST_TEXT = (
    "reboot   system boot  6.12.0 2026-03-10T08:00:00+00:00 - still running\n"
    "reboot   system boot  6.12.0 2026-03-05T09:00:00+00:00 - crash\n"
    "reboot   system boot  6.12.0 2026-03-01T07:00:00+00:00 - 2026-03-05T08:59:00+00:00\n"
    "reboot   system boot  6.12.0 2026-02-20T07:00:00+00:00 - crash\n"
    "reboot   system boot  6.12.0 2026-02-10T07:00:00+00:00 - 2026-02-20T06:59:00+00:00\n"
    "\n"
    "wtmp begins 2026-02-10T07:00:00+00:00\n"
)


def _us(dt: datetime) -> int:
    return int(dt.timestamp() * 1e6)


def _dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


def test_parse_last_reboots_kinds_and_links():
    events = power.parse_last_reboots(LAST_TEXT)
    assert [e.kind for e in events] == [
        POWER_UNEXPECTED,
        POWER_CLEAN,
        POWER_UNEXPECTED,
        POWER_CLEAN,
    ]
    crash = events[0]
    assert crash.boot_at == _dt("2026-03-05T09:00:00+00:00")
    assert crash.up_at == _dt("2026-03-10T08:00:00+00:00")
    assert crash.down_at is None and crash.down_approx is True
    clean = events[1]
    assert clean.down_at == _dt("2026-03-05T08:59:00+00:00")
    assert clean.up_at == crash.boot_at
    assert clean.down_approx is False
    assert events[3].up_at == events[2].boot_at


def test_parse_last_reboots_limit_and_garbage():
    assert len(power.parse_last_reboots(LAST_TEXT, limit=2)) == 2
    assert power.parse_last_reboots("") == []
    assert power.parse_last_reboots("reboot system boot x\nuser pts/0 foo") == []


def test_parse_journal_boots():
    first = _dt("2026-03-05T09:00:30+00:00")
    last = _dt("2026-03-09T12:00:00+00:00")
    text = json.dumps(
        [
            {"first_entry": _us(first), "last_entry": _us(last)},
            {"first_entry": "x", "last_entry": 1},
            {"last_entry": 1},
        ]
    )
    assert power.parse_journal_boots(text) == [(first, last)]
    assert power.parse_journal_boots("не json") == []


def test_enrich_unexpected_matches_within_tolerance():
    events = power.parse_last_reboots(LAST_TEXT)
    near = (_dt("2026-03-05T09:01:00+00:00"), _dt("2026-03-05T07:30:00+00:00"))
    far = (_dt("2026-02-20T07:10:00+00:00"), _dt("2026-02-19T00:00:00+00:00"))
    out = power.enrich_unexpected(events, [near, far])
    assert out[0].down_at == near[1]
    assert out[0].down_approx is True
    assert out[1] == events[1]
    assert out[2].down_at is None
    assert out[3] == events[3]


def _fake_run(monkeypatch, last_out, journal_out, calls=None):
    monkeypatch.setattr(power.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run(args, **kwargs):
        if calls is not None:
            calls.append(args[0])
        out = last_out if args[0] == "last" else journal_out
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    monkeypatch.setattr(power.subprocess, "run", run)


@pytest.fixture(autouse=True)
def _linux(monkeypatch):
    requirements_registry.reset()
    monkeypatch.setattr(requirements.host, "current", lambda: SimpleNamespace(name="linux"))
    yield
    requirements_registry.reset()


def test_read_power_events_pages(monkeypatch):
    _fake_run(monkeypatch, LAST_TEXT, "[]")
    page, has_next = power.read_power_events_sync(0, 3)
    assert len(page) == 3 and has_next is True
    page, has_next = power.read_power_events_sync(3, 3)
    assert len(page) == 1 and has_next is False
    page, has_next = power.read_power_events_sync(0, 4)
    assert len(page) == 4 and has_next is False
    page, has_next = power.read_power_events_sync(10, 3)
    assert page == [] and has_next is False


def test_read_power_events_enriches_with_journal(monkeypatch):
    last = _dt("2026-03-05T07:30:00+00:00")
    journal = json.dumps([{"first_entry": _us(_dt("2026-03-05T09:00:20+00:00")), "last_entry": _us(last)}])
    _fake_run(monkeypatch, LAST_TEXT, journal)
    page, _ = power.read_power_events_sync()
    assert page[0].down_at == last
    assert requirements_registry.status_for(power.JOURNALCTL_REQUIREMENT) is RequirementStatus.OK


def test_read_power_events_without_last(monkeypatch):
    monkeypatch.setattr(power.shutil, "which", lambda name: None)
    assert power.read_power_events_sync() == ([], False)


def test_read_power_events_last_fails_with_permission(monkeypatch):
    monkeypatch.setattr(power.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        power.subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 1, stdout="", stderr="Permission denied"),
    )
    assert power.read_power_events_sync() == ([], False)
    assert requirements_registry.status_for(power.LAST_REQUIREMENT) is RequirementStatus.NEEDS_PRIVILEGE


def test_run_oserror(monkeypatch):
    monkeypatch.setattr(power.shutil, "which", lambda name: "/x")

    def boom(*a, **k):
        raise OSError("нет")

    monkeypatch.setattr(power.subprocess, "run", boom)
    assert power._run(["last"]) is None


def test_read_uptime_sync():
    up = power.read_uptime_sync()
    assert up is not None and up > timedelta(0)
