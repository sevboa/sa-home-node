import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from sa_home_node.content import ContentStore
from sa_home_node.example import EchoHandler
from sa_home_node.plugins import Plugin, ServiceContext
from sa_home_node.proto.server import ProtoServer
from sa_home_node.proto.wire import ERR_UNKNOWN_ACTION, ProtoError
from sa_home_node.tasks.config import TasksConfig
from sa_home_node.tasks.kinds import TIMER, TaskKind
from sa_home_node.tasks.link import NodeLinkClient
from sa_home_node.tasks.run import make_handler, run_tasks

SCHEMA = json.loads((Path(__file__).parents[1] / "fixtures" / "tasks" / "db-schema.json").read_text())


def make_ctx(tmp_path, plugins=(), broadcast=None):
    return ServiceContext(
        node_id="n1",
        data_dir=tmp_path,
        config=TasksConfig(db_path=tmp_path / "tasks.sqlite"),
        content=ContentStore(tmp_path / "content"),
        plugins=plugins,
        node_socket=str(tmp_path / "node.sock"),
        swarm_token="t",
        broadcast=broadcast,
    )


async def wait_for(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("не дождались")


async def test_run_lifecycle(tmp_path):
    extra = TaskKind("act", execute=TIMER.execute)
    sink = []

    async def broadcast(event_type, data):
        if sink:
            return await sink[0](event_type, data)
        return 0

    ctx = make_ctx(tmp_path, plugins=(Plugin("x", task_kinds=(extra,)),), broadcast=broadcast)
    service = make_handler(ctx)
    ctx = replace(ctx, handler=service)
    assert set(service.runner._kinds) == {"timer", "act"}
    task = asyncio.create_task(run_tasks(ctx))
    await wait_for(lambda: service.runner._loops)
    loops = list(service.runner._loops)
    rows = await service.db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
    assert set(SCHEMA) <= {r["name"] for r in rows}

    sent = []

    async def record(event_type, data):
        sent.append((event_type, data))
        return 1

    created = await service.run_command(
        "create",
        {"due_at": "2030-01-01T00:00:00+00:00", "dst_node": "a", "dst_service": "s", "action": "timer"},
    )
    await service.run_command("fire_now", {"task_id": created["task_id"]})
    await asyncio.sleep(0.05)
    assert sent == []
    sink.append(record)
    await service.run_command(
        "create",
        {"due_at": "2030-01-01T00:00:00+00:00", "dst_node": "a", "dst_service": "s", "action": "timer"},
    )
    await service.run_command("fire_now", {"task_id": 2})
    await wait_for(lambda: sent)
    assert sent[0][0] == "task_result"
    assert sent[0][1]["task_id"] == 2

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(loop.done() for loop in loops)
    assert not service.runner._jobs
    assert service.db._conn is None
    await service.runner.stop()
    await service.link.close()
    await service.db.close()


async def test_link_connects_lazily_and_reconnects(tmp_path):
    sock = str(tmp_path / "s.sock")
    link = NodeLinkClient(sock, "t")
    with pytest.raises(ConnectionError):
        await link.get_state()
    handler = EchoHandler(SimpleNamespace(node_id="n"))
    server = ProtoServer(sock, handler)
    await server.start()
    assert await link.get_state() == {"count": 0}
    assert await link.command("echo", {"text": "x"}) == {"text": "x"}
    with pytest.raises(ProtoError) as info:
        await link.command("nope")
    assert info.value.code == ERR_UNKNOWN_ACTION
    await server.stop()
    server = ProtoServer(sock, handler)
    await server.start()
    assert await link.get_state() == {"count": 1}
    await server.stop()
    await link.close()
    await link.close()
