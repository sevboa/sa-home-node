from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from sa_home_node.db import Database
from sa_home_node.proto.wire import ERR_INTERNAL, ERR_UNAVAILABLE, Address, ProtoError
from sa_home_node.tasks import loops, service
from sa_home_node.tasks.config import TasksConfig
from sa_home_node.tasks.kinds import TIMER, TaskKind
from sa_home_node.tasks.loops import TaskRunner
from sa_home_node.tasks.service import TasksService
from sa_home_node.tasks.store import TaskStore, apply_schema
from sa_home_node.wake import ready
from sa_home_node.wake.peers import MemoryWakeCache

FLOW = json.loads((Path(__file__).parents[1] / "fixtures" / "tasks" / "flow.json").read_text())
REAL_SLEEP = asyncio.sleep
RECORDED = {"link.get_state", "link.command", "sleep", "chat_loop", "event"}


def outcome_error(text: str) -> Exception:
    match = re.fullmatch(r"([a-z_]+): (.*)", text)
    return ProtoError(match[1], match[2]) if match else ConnectionError(text)


class Harness:
    def __init__(self, scenario: dict) -> None:
        trace = scenario["trace"]
        self.now = 0.0
        self.base = datetime.fromisoformat(scenario["base"])
        self.actual: list[dict] = []
        self.states: dict[Any, deque] = defaultdict(deque)
        for pos, entry in enumerate(trace):
            if entry["kind"] == "link.get_state":
                after = trace[pos + 1] if pos + 1 < len(trace) else {}
                asleep = after.get("kind") == "link.command" and after.get("action") == "warmup"
                self.states[entry["dst"]].append((entry["ok"], asleep))
        self.wol = any(e.get("action") == "send_wol" for e in trace if e["kind"] == "link.command")
        creates = [e["args"][0]["action"] for e in trace if e.get("op") == "create"]
        results = [e["data"] for e in trace if e["kind"] == "event" and e["event"] == "task_result"]
        self.outcomes = {
            creates[r["task_id"] - 1]: r for r in results if r["task_id"] - 1 < len(creates)
        }
        self.warmups = deque(
            pos + 1 < len(trace) and trace[pos + 1]["kind"] != "step"
            for pos, e in enumerate(trace)
            if e["kind"] == "link.command" and e["action"] == "warmup"
        )
        self.chat_attempts = sum(1 for e in trace if e["kind"] == "chat_loop" and "attempt" in e)
        self.attempt = 0

    def record(self, kind: str, **fields: Any) -> None:
        self.actual.append({"t": self.now, "kind": kind, **fields})

    async def sleep(self, seconds: float) -> None:
        self.record("sleep", seconds=seconds)
        self.now += seconds
        await REAL_SLEEP(0)

    async def emit(self, event: str, data: dict) -> None:
        self.record("event", event=event, data=data)

    async def get_state(self, dst: Address | None = None) -> dict:
        key = f"{dst.node}/{dst.service}" if dst else None
        if key is None:
            return self.own_state()
        if not self.states[key]:
            raise AssertionError(f"неожиданный get_state {key}")
        ok, asleep = self.states[key].popleft()
        self.record("link.get_state", dst=key, ok=ok)
        if not ok:
            raise TimeoutError
        return {"asleep": asleep}

    def own_state(self) -> dict:
        self.record("link.get_state", dst=None, ok=True)
        peers = []
        if self.wol:
            peers = [{"id": "node-b", "alive": False, "wake": {"mac": "02:00:00:00:00:0b", "broadcast": "b"}}]
        return {"node": "node-a", "wake": {"mac": "02:00:00:00:00:0a", "broadcast": "b"}, "peers": peers}

    async def command(self, action, args=None, dst=None, *, timeout=None) -> dict:
        self.record(
            "link.command", action=action, args=args, dst=f"{dst.node}/{dst.service}", timeout=timeout
        )
        if action == "warmup" and not self.warmups.popleft():
            raise ProtoError(ERR_INTERNAL, "отказ")
        if action in ("send_wol", "warmup"):
            return {}
        result = self.outcomes[action]
        if not result["ok"]:
            raise outcome_error(result["error"])
        return result["result"]

    async def chat(self, call) -> dict:
        self.attempt += 1
        if self.chat_attempts:
            self.record("chat_loop", attempt=self.attempt)
        else:
            self.record(
                "chat_loop",
                timeout=call.timeout_s,
                messages=call.args.get("messages"),
                reason=call.args.get("reason") or "high",
            )
        result = self.outcomes["chat_loop"]
        if self.attempt < self.chat_attempts:
            raise ProtoError(ERR_UNAVAILABLE, "модель недоступна")
        if not result["ok"]:
            raise outcome_error(result["error"])
        return result["result"]


class FakeDatetime(datetime):
    harness: Harness

    @classmethod
    def now(cls, tz=None):
        return cls.harness.base + timedelta(seconds=cls.harness.now)


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "tasks.sqlite")
    await database.open()
    await apply_schema(database)
    yield database
    await database.close()


def chat_kind(harness: Harness) -> TaskKind:
    return TaskKind(
        "chat_loop",
        execute=harness.chat,
        wait_ready=True,
        retry=lambda exc: isinstance(exc, ProtoError) and exc.code == ERR_UNAVAILABLE,
    )


async def drain(runner: TaskRunner) -> None:
    while runner._jobs:
        await asyncio.gather(*list(runner._jobs))


@pytest.mark.parametrize("scenario", FLOW, ids=[s["scenario"] for s in FLOW])
async def test_flow(scenario, db, monkeypatch):
    harness = Harness(scenario)
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "time", lambda: harness.now)
    FakeDatetime.harness = harness
    monkeypatch.setattr(loops, "datetime", FakeDatetime)
    monkeypatch.setattr(service, "datetime", FakeDatetime)
    monkeypatch.setattr(ready.asyncio, "sleep", harness.sleep)
    monkeypatch.setattr(ready, "WAKE_POLL_TIMEOUT_S", 5.0)
    store = TaskStore(db)
    config = TasksConfig()
    runner = TaskRunner(
        harness,
        store,
        MemoryWakeCache(),
        [TIMER, chat_kind(harness)],
        harness.emit,
        config,
        sleep=harness.sleep,
        clock=lambda: harness.now,
    )
    svc = TasksService(config, store, runner, node="node-a")
    replies = []
    for entry in scenario["trace"]:
        if entry["kind"] != "step":
            continue
        op, args = entry["op"], entry["args"]
        if op == "advance":
            harness.now += args[0]
        elif op == "drain":
            await drain(runner)
        elif op == "prewake_due":
            await runner.prewake_due()
        elif op == "fire_due":
            await runner.fire_due()
        else:
            payloads = {
                "create": args[0] if op == "create" else None,
                "fire_now": {"task_id": args[0]},
                "match_event": dict(zip(("node", "event_type"), args)),
            }
            payload = payloads[op]
            replies.append({"action": op, "reply": await svc.run_command(op, payload)})
            await REAL_SLEEP(0)
    await drain(runner)
    expected = [e for e in scenario["trace"] if e["kind"] in RECORDED]
    assert harness.actual == expected
    assert replies == [{"action": e["action"], "reply": e["reply"]} for e in scenario["trace"] if e["kind"] == "reply"]
    rows = await db.fetch_all("SELECT * FROM tasks ORDER BY id")
    assert [dict(r) for r in rows] == scenario["db"]["tasks"]
    waiters = await db.fetch_all("SELECT * FROM event_waiters ORDER BY task_id")
    assert [dict(r) for r in waiters] == scenario["db"]["event_waiters"]
    waking = [e["data"]["task_id"] for e in expected if e["kind"] == "event" and e["data"].get("status") == "waking"]
    assert {str(k): waking.count(k) for k in set(waking)} == scenario["waking_per_task"]


async def test_loops_sleep_before_first_tick():
    ticks = []
    sleeps = []

    class Store:
        async def tasks_needing_prewake(self, deadline):
            ticks.append("prewake")
            return []

        async def due_tasks(self, now):
            ticks.append("fire")
            return []

    async def sleep(seconds):
        sleeps.append(seconds)
        await REAL_SLEEP(0)

    runner = TaskRunner(None, Store(), MemoryWakeCache(), [], None, TasksConfig(), sleep=sleep)
    runner.start()
    assert ticks == []
    await REAL_SLEEP(0.01)
    await runner.stop()
    assert sleeps[:2] == [30.0, 30.0]
    assert sorted(set(ticks)) == ["fire", "prewake"]


async def test_stop_leaves_no_tasks():
    runner = TaskRunner(None, None, MemoryWakeCache(), [], None, TasksConfig())
    runner.start()
    await runner.stop()
    current = asyncio.current_task()
    assert [t for t in asyncio.all_tasks() if t is not current and not t.done()] == []
