from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from support import StubService, wait_for

from sa_home_node import __version__
from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.describe import ActionParam, ActionSpec
from sa_home_node.proto.server import ProtoServer
from sa_home_node.proto.wire import Address, Envelope
from sa_home_node.services.supervisor import process_argv

pytestmark = [pytest.mark.skipif(sys.platform != "linux", reason="unix"), pytest.mark.timeout(60)]

FIXTURES = Path(__file__).parents[1] / "fixtures" / "tasks"
SRC = str(Path(__file__).parents[2] / "src")
TARGET = Address(node="node-b", service="svc")


class Target(StubService):
    def __init__(self) -> None:
        super().__init__(
            node="node-b",
            service="svc",
            actions=(ActionSpec(id="do", title="Сделать", params=(ActionParam(name="x", required=False),)),),
        )
        self.seen: list[tuple[str, dict]] = []

    async def run_command(self, action: str, args: dict) -> dict:
        self.seen.append((action, args))
        return {"done": action}


class Live:
    def __init__(self, proc: asyncio.subprocess.Process, client: ProtoClient, results: list[dict]) -> None:
        self.proc = proc
        self.client = client
        self.results = results
        self.target = Target()

    async def create(self, action: str = "timer", *, due_in: float = 3600, **extra) -> int:
        due = datetime.now(tz=UTC) + timedelta(seconds=due_in)
        args = {"due_at": due.isoformat(), "dst_node": "node-b", "dst_service": "svc", "action": action, **extra}
        return (await self.client.command("create", args))["task_id"]

    async def result(self, task_id: int, timeout: float = 15.0) -> dict:
        assert await wait_for(lambda: any(r["task_id"] == task_id for r in self.results), timeout)
        return next(r for r in self.results if r["task_id"] == task_id)


async def launch(tmp_path, sock_dir, monkeypatch, *, seeded: bool = False):
    monkeypatch.setenv("PYTHONPATH", SRC)
    node_sock = f"{sock_dir}/node.sock"
    tasks_sock = f"{sock_dir}/tasks.sock"
    db = tmp_path / "tasks.sqlite"
    if seeded:
        shutil.copy(FIXTURES / "seeded-tasks.sqlite", db)
        with sqlite3.connect(db) as conn:
            past = (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat()
            conn.execute("UPDATE tasks SET due_at = ? WHERE id = 1", (past,))
    (tmp_path / "content").mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[node]\nid = "node-a"\nsocket = "{node_sock}"\nstate_path = "{tmp_path}/state.json"\n'
        f'data_dir = "{tmp_path}/data"\ncontent_dir = "{tmp_path}/content"\n'
        f'[tasks]\nsocket = "{tasks_sock}"\ndb_path = "{db}"\n'
        f'[swarm]\ntoken = "t"\n'
    )
    target = Target()
    server = ProtoServer(node_sock, target)
    await server.start()
    proc = await asyncio.create_subprocess_exec(*process_argv("tasks", "", str(cfg)))
    assert await wait_for(lambda: os.path.exists(tasks_sock), timeout=20)
    results: list[dict] = []

    async def on_event(env: Envelope) -> None:
        if env.payload.get("event") == "task_result":
            results.append(env.payload["data"])

    client = ProtoClient(tasks_sock, token="t", on_event=on_event)
    await client.connect()
    live = Live(proc, client, results)
    live.target = target
    return live, server


async def shutdown(live: Live, server: ProtoServer) -> None:
    await live.client.close()
    if live.proc.returncode is None:
        live.proc.kill()
    await live.proc.wait()
    await server.stop()


async def test_describe_forward_event_wakeup_and_sigterm(tmp_path, sock_dir, monkeypatch) -> None:
    live, server = await launch(tmp_path, sock_dir, monkeypatch)
    try:
        ref = json.loads((FIXTURES / "describe.json").read_text(encoding="utf-8"))
        description = await live.client.describe()
        assert description.to_payload() == {**ref["describe"], "version": __version__}
        assert await live.client.get_state() == ref["get_state"]

        forwarded = await live.create("do", args={"x": 1}, meta={"ref": 7})
        assert await live.client.command("fire_now", {"task_id": forwarded}) == {"fired": True}
        assert await live.result(forwarded) == {
            "task_id": forwarded,
            "meta": {"ref": 7},
            "ok": True,
            "result": {"done": "do"},
        }
        assert live.target.seen == [("do", {"x": 1})]

        waiting = await live.create(await_event={"node": "node-b", "event_type": "up"})
        assert await live.client.command("match_event", {"node": "node-b", "event_type": "up"}) == {
            "matched": True,
            "task_id": waiting,
        }
        assert await live.result(waiting) == {"task_id": waiting, "meta": {}, "ok": True, "result": {}}

        started = time.monotonic()
        live.proc.send_signal(signal.SIGTERM)
        assert await asyncio.wait_for(live.proc.wait(), timeout=10) == 0
        assert time.monotonic() - started < 10
    finally:
        await shutdown(live, server)


async def test_due_timer_and_overdue_seeded_task_fire_on_poll(tmp_path, sock_dir, monkeypatch) -> None:
    live, server = await launch(tmp_path, sock_dir, monkeypatch, seeded=True)
    try:
        timer = await live.create(due_in=1)
        assert await live.result(timer, timeout=45) == {"task_id": timer, "meta": {}, "ok": True, "result": {}}
        assert await live.result(1, timeout=5) == {"task_id": 1, "meta": {}, "ok": True, "result": {"done": "do"}}
        assert {r["task_id"] for r in live.results} == {1, timer}
    finally:
        await shutdown(live, server)
