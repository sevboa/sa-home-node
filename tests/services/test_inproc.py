from __future__ import annotations

import asyncio

import pytest
from support import Collector, StubService, wait_for

from sa_home_node.content import ContentStore
from sa_home_node.plugins import ServiceContext, ServiceFenced, ServiceSpec
from sa_home_node.proto.wire import (
    ERR_UNKNOWN_ACTION,
    MSG_COMMAND,
    MSG_GET_STATE,
    Address,
    ProtoError,
    make_request,
)
from sa_home_node.services.events import (
    EVENT_SERVICE_FAILED,
    EVENT_SERVICE_FENCED,
    EVENT_SERVICE_STARTED,
    EVENT_SERVICE_STOPPED,
    FENCED_EXIT_CODE,
    RUNNING,
    STOPPED,
)
from sa_home_node.services.inproc import InProcessService, InProcessTarget
from sa_home_node.swarm.router import NodeRouter


def make(tmp_path, run, fail_on="", **kw):
    collector = Collector(raise_on=fail_on)
    ctx = ServiceContext(node_id="node-a", data_dir=tmp_path / "svc", config=None, content=ContentStore(tmp_path))
    kw.setdefault("restart_delay_s", 0.01)
    return InProcessService(ServiceSpec(name="svc", run=run), ctx, emit=collector, **kw), collector


async def sleep_forever(ctx):
    await asyncio.sleep(3600)


async def test_crash_restarts_and_on_spawned_precedes_every_run(tmp_path):
    spawned, calls = [], []

    async def run(ctx):
        calls.append(len(spawned))
        if len(calls) == 1:
            raise RuntimeError("boom")
        await sleep_forever(ctx)

    svc, collector = make(tmp_path, run, on_spawned=spawned.append)
    await svc.start()
    assert await wait_for(lambda: svc.restarts == 1 and len(calls) == 2, 2)
    assert (spawned, calls, svc.started_at is not None) == (["svc", "svc"], [1, 2], True)
    assert collector.of(EVENT_SERVICE_STARTED)[0] == {"name": "svc", "pid": None}
    assert collector.of(EVENT_SERVICE_FAILED) == [{"name": "svc", "error": "boom"}]
    await svc.stop()


async def test_fenced_stops_without_restart(tmp_path):
    fenced, calls = [], []

    async def run(ctx):
        calls.append(1)
        raise ServiceFenced

    svc, collector = make(tmp_path, run, on_fenced=fenced.append)
    await svc.start()
    await collector.seen(EVENT_SERVICE_FENCED)
    assert (svc.status, svc.last_exit_code, fenced, calls, svc.process_alive) == (STOPPED, FENCED_EXIT_CODE, ["svc"], [1], False)
    await svc.start()
    assert await wait_for(lambda: calls == [1, 1], 2)
    assert fenced == ["svc", "svc"]


async def test_normal_return_stops(tmp_path):
    calls = []

    async def run(ctx):
        calls.append(1)

    svc, collector = make(tmp_path, run)
    await svc.start()
    await collector.seen(EVENT_SERVICE_STOPPED)
    assert (svc.status, calls, collector.of(EVENT_SERVICE_FAILED), svc.process_alive) == (STOPPED, [1], [], False)
    await svc.start()
    assert calls == [1]


async def test_stop_cancels_task(tmp_path):
    svc, collector = make(tmp_path, sleep_forever, stop_timeout_s=0.5)
    await svc.start()
    await collector.seen(EVENT_SERVICE_STARTED)
    assert (svc.status, svc.process_alive) == (RUNNING, True)
    await svc.stop()
    assert (svc.status, svc.process_alive) == (STOPPED, False)
    assert collector.of(EVENT_SERVICE_STOPPED) == [{"name": "svc"}]


@pytest.mark.parametrize("broken", ["emit", "on_spawned"])
async def test_broken_callbacks_do_not_break_run(tmp_path, broken):
    calls = []

    async def run(ctx):
        calls.append(1)
        if broken == "emit":
            raise RuntimeError("boom")
        await sleep_forever(ctx)

    def raising(name):
        raise RuntimeError("boom")

    options = {"fail_on": EVENT_SERVICE_FAILED} if broken == "emit" else {"on_spawned": raising}
    svc, _ = make(tmp_path, run, **options)
    await svc.start()
    assert await wait_for(lambda: len(calls) >= 2 if broken == "emit" else calls == [1], 2)
    await svc.stop()


async def test_cancelled_stop_cancels_stubborn_task(tmp_path):
    async def run(ctx):
        try:
            await sleep_forever(ctx)
        except asyncio.CancelledError:
            await sleep_forever(ctx)

    svc, collector = make(tmp_path, run, stop_timeout_s=30.0)
    await svc.start()
    await collector.seen(EVENT_SERVICE_STARTED)
    task = svc._task
    stopping = asyncio.create_task(svc.stop())
    await asyncio.sleep(0.05)
    assert not task.done()
    stopping.cancel()
    await asyncio.gather(stopping, return_exceptions=True)
    assert await wait_for(task.done, 2)
    assert svc.status == STOPPED
    await asyncio.wait_for(svc.stop(), 2.0)


async def test_target_routes_get_state_and_rejects_unknown_action(tmp_path):
    svc, _ = make(tmp_path, sleep_forever)
    await svc.start()
    assert await wait_for(lambda: svc.process_alive, 2)
    handler = StubService(node="node-a", service="svc", busy=True, working=False, state={"x": 1})
    target = InProcessTarget(svc, handler)
    assert (target.name, target.alive, target.busy, target.working) == ("svc", True, True, False)
    router = NodeRouter("node-a", peers={}, local_services={"svc": target})
    response = await router.route(make_request(MSG_GET_STATE, dst=Address(node="node-a", service="svc")))
    assert response.payload == {"x": 1}
    with pytest.raises(ProtoError) as exc:
        await target.forward(make_request(MSG_COMMAND, {"action": "nope", "args": {}}))
    assert exc.value.code == ERR_UNKNOWN_ACTION
    assert "nope" in exc.value.message
    await svc.stop()
