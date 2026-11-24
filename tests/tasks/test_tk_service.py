from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sa_home_node.proto.wire import ERR_BAD_REQUEST, ERR_UNKNOWN_ACTION, ProtoError
from sa_home_node.tasks.config import TasksConfig
from sa_home_node.tasks.service import TasksService


class FakeStore:
    def __init__(self):
        self.rows = {}
        self.waiters = {}
        self.created = []
        self.next_id = 1

    async def create_task(self, *params):
        self.created.append(params)
        task_id = self.next_id
        self.next_id += 1
        self.rows[task_id] = {"id": task_id, "fired": False}
        return task_id

    async def add_event_waiter(self, task_id, node, event_type, created_at):
        self.waiters[task_id] = (node, event_type)

    async def get_pending_task(self, task_id):
        row = self.rows.get(task_id)
        return row if row and not row["fired"] else None

    async def mark_task_fired(self, task_id, at):
        self.rows[task_id]["fired"] = True

    async def pop_event_waiter(self, task_id):
        self.waiters.pop(task_id, None)

    async def pop_event_waiter_for(self, node, event_type):
        for task_id, key in sorted(self.waiters.items()):
            if key == (node, event_type):
                del self.waiters[task_id]
                return task_id
        return None


class FakeRunner:
    def __init__(self):
        self.fired = []

    def fire(self, row):
        self.fired.append(row["id"])


@pytest.fixture
def parts():
    store, runner = FakeStore(), FakeRunner()
    service = TasksService(TasksConfig(), store, runner, node="n1")
    return service, store, runner


BASE = {"due_at": "2030-01-01T10:00:00+00:00", "dst_node": "a", "dst_service": "s", "action": "x"}


async def test_describe_and_state(parts):
    service, _, _ = parts
    payload = service.describe().to_payload()
    assert payload["service"] == "tasks"
    assert payload["node"] == "n1"
    assert [a["id"] for a in payload["actions"]] == ["create", "fire_now", "match_event"]
    assert [p["name"] for p in payload["actions"][0]["params"]] == [
        "due_at",
        "dst_node",
        "dst_service",
        "action",
        "args",
        "timeout_s",
        "meta",
        "await_event",
    ]
    assert await service.get_state() == {"node": "n1", "service": "tasks"}


async def test_create_defaults_and_waiter(parts):
    service, store, _ = parts
    reply = await service.run_command("create", {**BASE, "await_event": {"node": "b", "event_type": "e"}})
    assert reply == {"task_id": 1}
    _, _, _, args, timeout_s, meta, due_at, _ = store.created[0]
    assert (args, meta, timeout_s) == ({}, {}, 240.0)
    assert due_at == datetime(2030, 1, 1, 10, tzinfo=UTC)
    assert store.waiters == {1: ("b", "e")}


async def test_create_naive_due_is_local(parts):
    service, store, _ = parts
    await service.run_command("create", {**BASE, "due_at": "2030-01-01T10:00:00", "timeout_s": 5})
    due_at = store.created[0][6]
    assert due_at.tzinfo is not None
    assert store.created[0][4] == 5.0


@pytest.mark.parametrize(
    "patch, text",
    [
        ({"due_at": ""}, "обязательны"),
        ({"dst_node": 3}, "обязательны"),
        ({"due_at": "вчера"}, "due_at не в формате ISO 8601"),
        ({"args": []}, "args должен быть объектом"),
        ({"meta": "m"}, "meta должен быть объектом"),
        ({"timeout_s": "1"}, "timeout_s должен быть числом"),
        ({"await_event": {"node": "b"}}, "await_event должен быть"),
    ],
)
async def test_create_rejects(parts, patch, text):
    service, store, _ = parts
    with pytest.raises(ProtoError) as exc:
        await service.run_command("create", {**BASE, **patch})
    assert exc.value.code == ERR_BAD_REQUEST
    assert text in exc.value.message
    assert not store.created


async def test_fire_now_once(parts):
    service, _, runner = parts
    await service.run_command("create", BASE)
    assert await service.run_command("fire_now", {"task_id": 1}) == {"fired": True}
    assert await service.run_command("fire_now", {"task_id": 1}) == {
        "fired": False,
        "reason": "нет такой задачи или уже сработала",
    }
    assert runner.fired == [1]


async def test_fire_now_bad_id(parts):
    service, _, _ = parts
    with pytest.raises(ProtoError) as exc:
        await service.run_command("fire_now", {"task_id": "1"})
    assert exc.value.code == ERR_BAD_REQUEST


async def test_match_event(parts):
    service, store, runner = parts
    await service.run_command("create", {**BASE, "await_event": {"node": "b", "event_type": "e"}})
    assert await service.run_command("match_event", {"node": "x", "event_type": "e"}) == {"matched": False}
    assert await service.run_command("match_event", {"node": "b", "event_type": "e"}) == {
        "matched": True,
        "task_id": 1,
    }
    assert runner.fired == [1]
    assert store.waiters == {}
    with pytest.raises(ProtoError):
        await service.run_command("match_event", {"node": "", "event_type": "e"})


async def test_unknown_action(parts):
    service, _, _ = parts
    with pytest.raises(ProtoError) as exc:
        await service.run_command("nope", {})
    assert exc.value.code == ERR_UNKNOWN_ACTION
