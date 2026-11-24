import asyncio
import dataclasses
import sqlite3
from pathlib import Path
from typing import ClassVar

import pytest

from sa_home_node.config import load_config
from sa_home_node.content import ContentStore
from sa_home_node.monitor import run as run_module
from sa_home_node.monitor.plugin import PLUGIN
from sa_home_node.monitor.scan import SensorScanJob
from sa_home_node.monitor.smartscan import SmartScanJob
from sa_home_node.plugins import Plugin, PluginError, load_plugins, make_context


class FakeWorker:
    jobs: ClassVar[list] = []
    contexts: ClassVar[list] = []

    def __init__(self, queue, ctx):
        self.queue = queue
        FakeWorker.contexts.append(ctx)

    async def run(self):
        while (job := await self.queue.get()) is not None:
            FakeWorker.jobs.append(type(job))
            self.queue.task_done()
        self.queue.task_done()


class FakeEndpoint:
    events: ClassVar[list] = []

    def __init__(self, render, port):
        self.port = port

    async def start(self):
        FakeEndpoint.events.append(("start", self.port))

    async def stop(self):
        FakeEndpoint.events.append(("stop", self.port))


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeWorker.jobs, FakeWorker.contexts, FakeEndpoint.events = [], [], []
    monkeypatch.setattr(run_module, "JobWorker", FakeWorker)
    monkeypatch.setattr(run_module, "MetricsEndpoint", FakeEndpoint)


def _ctx(tmp_path: Path, extra: str = ""):
    path = tmp_path / "config.toml"
    path.write_text(
        f'[node]\nid = "n1"\n\n[monitor]\ndb_path = "{tmp_path}/m.sqlite"\nsocket = "{tmp_path}/m.sock"\n{extra}'
    )
    content = tmp_path / "content"
    content.mkdir()
    ctx = make_context(PLUGIN, load_config(path), ContentStore(content), plugins=[PLUGIN])
    return dataclasses.replace(ctx, handler=PLUGIN.services[0].handler(ctx), broadcast=_broadcast)


async def _broadcast(event_type, data):
    return 0


async def _run_and_stop(ctx):
    task = asyncio.create_task(run_module.run_monitor(ctx))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(FakeWorker.jobs) >= 2:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run_applies_schema_enqueues_and_stops_clean(tmp_path):
    ctx = _ctx(tmp_path)
    await _run_and_stop(ctx)
    assert FakeWorker.jobs == [SmartScanJob, SensorScanJob]
    assert FakeEndpoint.events == []
    assert ctx.handler.store.db._conn is None
    with sqlite3.connect(tmp_path / "m.sqlite") as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"job_runs", "health_states"} <= tables
    await ctx.handler.store.db.close()


async def test_top_level_metrics_section_reaches_jobs(tmp_path):
    ctx = _ctx(tmp_path, "\n[metrics]\nexport = true\nexporter_port = 9191\n")
    await _run_and_stop(ctx)
    assert FakeWorker.contexts[0].metrics.export is True
    assert FakeEndpoint.events == [("start", 9191), ("stop", 9191)]


async def test_bad_cron_fails_at_start(tmp_path):
    ctx = _ctx(tmp_path, '\n[schedule]\nscan_cron = "bad"\n')
    with pytest.raises(ValueError, match="cron"):
        await run_module.run_monitor(ctx)
    assert FakeWorker.jobs == []
    assert ctx.handler.store.db._conn is None


def test_load_plugins_has_monitor_first():
    assert load_plugins([]) == [PLUGIN]
    assert isinstance(PLUGIN, Plugin)


def test_monitor_name_repeat_rejected():
    with pytest.raises(PluginError, match="Повтор"):
        load_plugins(["monitor"])
