from __future__ import annotations

import asyncio
import json
import logging
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import Clock

from sa_home_node import promexport
from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.metrics.filesd import file_sd_text
from sa_home_node.metrics.hub import INTERVAL_S, MetricsHub, run_hub
from sa_home_node.metrics.targets import ScrapeTarget
from sa_home_node.swarm.config import SwarmPeer

FIXTURES = Path(__file__).parent.parent / "fixtures" / "metrics"
CORE = json.loads((FIXTURES / "runtime" / "files-core.json").read_text(encoding="utf-8"))
PEERS = [SwarmPeer(**row) for row in CORE["peers"]]
OWN_HOST = CORE["own_host"]
UPDATED = "Метрики: обновлены файлы"


class FakeSwarm:
    def __init__(self, peers: list[SwarmPeer]) -> None:
        self.items = list(peers)

    def peers(self) -> list[SwarmPeer]:
        return list(self.items)


def make_ctx(root: Path, peers: list[SwarmPeer] = PEERS) -> SimpleNamespace:
    config = MetricsConfig(**{**CORE["config"], "runtime_dir": str(root)})
    return SimpleNamespace(node_id=CORE["own_id"], config=config, swarm=FakeSwarm(peers), plugins=())


def read_files(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): path.read_text(encoding="utf-8") for path in root.rglob("*.json")}


def updates(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage().startswith(UPDATED)]


def without_own_node() -> dict[str, str]:
    targets = [ScrapeTarget("n2", "100.64.0.2"), ScrapeTarget("n3", "192.168.1.3")]
    return {**CORE["files"], "targets/nodes.json": file_sd_text(targets, CORE["config"]["exporter_port"])}


@pytest.mark.parametrize(
    ("host", "expected"),
    [(OWN_HOST, CORE["files"]), (None, without_own_node())],
    ids=["overlay-address", "no-overlay-address"],
)
async def test_tick_writes_runtime_files(tmp_path, caplog, host, expected):
    caplog.set_level(logging.INFO)
    await MetricsHub(make_ctx(tmp_path), address=lambda: host).tick()
    assert read_files(tmp_path) == expected
    assert len(updates(caplog)) == 1
    assert "nodes.json" in updates(caplog)[0].getMessage()


async def test_second_tick_changes_nothing(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    hub = MetricsHub(make_ctx(tmp_path), address=lambda: OWN_HOST)
    await hub.tick()
    stamps = {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*.json")}
    await hub.tick()
    assert {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*.json")} == stamps
    assert len(updates(caplog)) == 1


async def test_tick_follows_swarm_peers(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    ctx = make_ctx(tmp_path)
    hub = MetricsHub(ctx, address=lambda: OWN_HOST)
    await hub.tick()
    ctx.swarm.items = PEERS[:1]
    await hub.tick()
    nodes = json.loads((tmp_path / "targets" / "nodes.json").read_text(encoding="utf-8"))
    assert [group["labels"]["node"] for group in nodes] == ["n1", "n2"]
    assert len(updates(caplog)) == 2


async def test_write_error_is_logged_and_next_tick_recovers(tmp_path, caplog):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    hub = MetricsHub(make_ctx(blocker / "rt"), address=lambda: OWN_HOST)
    await hub.tick()
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings
    assert warnings[0].exc_info is not None
    assert not updates(caplog)
    blocker.unlink()
    await hub.tick()
    assert read_files(blocker / "rt") == CORE["files"]


async def test_start_ticks_at_once_then_waits_interval(tmp_path):
    clock = Clock()
    hub = MetricsHub(make_ctx(tmp_path), address=lambda: OWN_HOST, sleep=clock.sleep)
    await hub.start()
    assert read_files(tmp_path) == CORE["files"]
    for _ in range(5):
        await asyncio.sleep(0)
    assert clock.sleeps[0] == INTERVAL_S == 60.0
    await hub.stop()


async def test_run_hub_writes_and_stops_on_cancel(tmp_path, monkeypatch):
    ifaces = {"tun0": [SimpleNamespace(family=socket.AF_INET, address=OWN_HOST)]}
    monkeypatch.setattr(promexport.psutil, "net_if_addrs", lambda: ifaces)
    monkeypatch.setattr(promexport, "is_tun", lambda iface: iface == "tun0")
    task = asyncio.create_task(run_hub(make_ctx(tmp_path)))
    for _ in range(200):
        if (tmp_path / "dashboards" / "nodes.json").exists():
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert read_files(tmp_path) == CORE["files"]
    assert not [item for item in asyncio.all_tasks() if item.get_name() == "metrics-hub"]
