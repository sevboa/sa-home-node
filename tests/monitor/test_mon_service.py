from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from sa_home_node import requirements
from sa_home_node.monitor import service as service_module
from sa_home_node.monitor.config import SensorsConfig
from sa_home_node.monitor.db import Database
from sa_home_node.monitor.jobbase import DedupQueue
from sa_home_node.monitor.models import PowerEvent
from sa_home_node.monitor.service import MonitorService
from sa_home_node.monitor.store import MonitorStore
from sa_home_node.proto.wire import ERR_UNKNOWN_ACTION, ProtoError
from sa_home_node.requirements import RequirementStatus

FIXTURES = Path(__file__).parents[1] / "fixtures" / "monitor" / "service"
NOW = datetime(2026, 3, 1, 1, 20, tzinfo=UTC)
UPTIME = timedelta(seconds=273605)
HOST_ON = {"host": {"enabled": True}}
VPS_SETTINGS = {
    "cpu": {"warn_c": 70, "crit_c": 85},
    "gpu": {"warn_c": 75, "crit_c": 88},
    "disks": {"warn_c": 50, "crit_c": 60},
    "host": {
        "enabled": True,
        "thresholds": {"steal_pct": {"warn": 5, "crit": 15}, "mem_available_pct": {"warn": 20, "crit": 10}},
    },
}
STATE_SCENARIOS = {
    "ok": ({}, {}, False),
    "missing-programs": (
        {"gpu": {"enabled": True}, **HOST_ON},
        {
            "smartctl": "missing_program",
            "nvidia-smi": "missing_program",
            "journalctl-kernel": "needs_privilege",
        },
        False,
    ),
    "last-needs-privilege": ({}, {"smartctl": "needs_privilege", "last": "needs_privilege"}, False),
    "unsupported-platform": (
        HOST_ON,
        {"last": "unsupported_platform", "journalctl-kernel": "unsupported_platform"},
        False,
    ),
    "vps-no-outage": (VPS_SETTINGS, {}, True),
    "host-disabled-with-seeded-states": ({"host": {"enabled": False}}, {}, False),
    "host-enabled-with-seeded-states": (HOST_ON, {}, False),
}
GET_STATE = json.loads((FIXTURES / "get-state.json").read_text(encoding="utf-8"))
THRESHOLDS = json.loads((FIXTURES / "thresholds.json").read_text(encoding="utf-8"))
REPLIES = json.loads((FIXTURES / "replies.json").read_text(encoding="utf-8"))
DESCRIBE = json.loads((FIXTURES / "describe.json").read_text(encoding="utf-8"))


def _events() -> list[PowerEvent]:
    full = next(r for r in REPLIES if r["args"] == {"limit": 100})["reply"]["events"]
    iso = datetime.fromisoformat
    return [
        PowerEvent(
            e["kind"],
            iso(e["boot_at"]),
            e["down_at"] and iso(e["down_at"]),
            e["up_at"] and iso(e["up_at"]),
            e["down_approx"],
        )
        for e in full
    ]


EVENTS = _events()


@pytest.fixture
async def make(tmp_path, monkeypatch):
    path = tmp_path / "monitor.sqlite"
    shutil.copy(FIXTURES / "seeded-monitor.sqlite", path)
    db = Database(path)
    await db.open()
    monkeypatch.setattr(service_module, "utc_now", lambda: NOW)
    monkeypatch.setattr(service_module.socket, "gethostname", lambda: "node-a")
    monkeypatch.setattr(service_module, "read_uptime_sync", lambda: UPTIME)

    def read_events(offset=0, limit=10):
        return EVENTS[offset : offset + limit], offset + limit < len(EVENTS)

    monkeypatch.setattr(service_module, "read_power_events_sync", read_events)
    monkeypatch.setattr(requirements, "_install_command", lambda pkg: f"sudo apt install {pkg}")

    def build(settings=None, providers=()):
        return MonitorService(
            SensorsConfig.model_validate(settings or {}),
            MonitorStore(db),
            DedupQueue(),
            providers,
            version="0.0.0",
        )

    yield build
    await db.close()


async def test_describe_matches_fixture(make):
    assert make().describe().to_payload() == DESCRIBE


@pytest.mark.parametrize("name", list(STATE_SCENARIOS))
async def test_get_state(make, monkeypatch, name):
    settings, statuses, no_outage = STATE_SCENARIOS[name]
    monkeypatch.setattr(
        requirements.requirements_registry,
        "status_for",
        lambda req: RequirementStatus(statuses.get(req.key or req.program, "ok")),
    )
    monkeypatch.setattr(requirements.host, "current", lambda: SimpleNamespace(name="windows"))
    if no_outage:
        monkeypatch.setattr(service_module, "read_uptime_sync", lambda: None)
        monkeypatch.setattr(service_module, "read_power_events_sync", lambda offset=0, limit=10: ([], False))
    state = await make(settings).get_state()
    assert state == GET_STATE[name]


@pytest.mark.parametrize("name", list(THRESHOLDS))
async def test_thresholds(make, name):
    settings = STATE_SCENARIOS[name][0]
    service = make(settings)
    state = await service.get_state()
    expected = THRESHOLDS[name]
    assert state["thresholds"] == expected["thresholds"]
    assert state["host_thresholds"] == expected["host_thresholds"]
    assert service._host_thresholds() == expected["host_thresholds_direct"]


async def test_provider_problems(make):
    provider = SimpleNamespace(problem=lambda: {"id": "p", "status": "missing_program", "hint": "h"})
    quiet = SimpleNamespace(problem=lambda: None)
    state = await make({}, [quiet, provider]).get_state()
    assert state["requirements"][-1] == {"id": "p", "status": "missing_program", "hint": "h"}


async def test_replies(make):
    service = make()
    for case in REPLIES:
        if case["action"] == "scan_now" and case is REPLIES[2]:
            await service.queue.get()
            await service.queue.get()
        if "error" in case:
            with pytest.raises(ProtoError) as exc:
                await service.run_command(case["action"], case["args"])
            assert exc.value.code == ERR_UNKNOWN_ACTION
            assert exc.value.message == case["error"]
            continue
        assert await service.run_command(case["action"], case["args"]) == case["reply"]
