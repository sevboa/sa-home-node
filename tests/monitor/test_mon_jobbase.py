from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from sa_home_node.monitor.jobbase import DedupQueue, JobContext, JobResult, JobWorker


@dataclass
class FakeJob:
    dedup_key: str
    job_type: str = "sensor_scan"
    fail: bool = False
    gate: asyncio.Event | None = None

    async def run(self, ctx):
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("сбой")
        return JobResult(components_scanned=2, extra={"k": "я"})


class FakeStore:
    def __init__(self):
        self.runs = {}
        self.next_id = 0

    async def start_job_run(self, job_type, started_at):
        self.next_id += 1
        self.runs[self.next_id] = {"type": job_type, "status": "running"}
        return self.next_id

    async def finish_job_run(self, run_id, status, finished_at, error=None, metrics_json=None):
        self.runs[run_id].update(status=status, error=error, metrics=metrics_json)


def make_ctx(store):
    return JobContext(store=store, sensors=None, dispatcher=None, config=None)


def test_result_json():
    result = JobResult(1, 2, 3, 4, {"x": "я"})
    assert result.to_json() == (
        '{"components_scanned": 1, "transitions": 2, "alerts_sent": 3, "clears_sent": 4, "extra": {"x": "я"}}'
    )
    assert json.loads(JobResult().to_json())["extra"] == {}


async def test_dedup_key_released_on_get():
    queue = DedupQueue()
    assert await queue.put(FakeJob("a")) is True
    assert await queue.put(FakeJob("a")) is False
    assert await queue.put(FakeJob("b")) is True
    assert queue.qsize() == 2
    await queue.get()
    assert await queue.put(FakeJob("a")) is True


async def test_worker_runs_sequentially_and_survives_failure():
    store = FakeStore()
    queue = DedupQueue()
    worker = asyncio.create_task(JobWorker(queue, make_ctx(store)).run())
    await queue.put(FakeJob("bad", fail=True))
    await queue.put(FakeJob("good", job_type="smart_scan"))
    await queue.stop()
    await asyncio.wait_for(worker, 2)
    assert store.runs[1]["status"] == "error"
    assert store.runs[1]["error"] == "сбой"
    assert store.runs[2]["type"] == "smart_scan"
    assert store.runs[2]["status"] == "ok"
    assert json.loads(store.runs[2]["metrics"])["components_scanned"] == 2


async def test_running_record_visible_during_job():
    store = FakeStore()
    queue = DedupQueue()
    gate = asyncio.Event()
    worker = asyncio.create_task(JobWorker(queue, make_ctx(store)).run())
    await queue.put(FakeJob("a", gate=gate))
    await asyncio.sleep(0.05)
    assert store.runs[1]["status"] == "running"
    assert await queue.put(FakeJob("a")) is True
    gate.set()
    await queue.stop()
    await asyncio.wait_for(worker, 2)
    assert [r["status"] for r in store.runs.values()] == ["ok", "ok"]
