from __future__ import annotations

import asyncio

import pytest
from support import StubService, wait_for

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


def make_ctx(tmp_path):
    return ServiceContext(
        node_id="node-a",
        data_dir=tmp_path / "svc",
        config=None,
        content=ContentStore(tmp_path),
    )


class Recorder:
    def __init__(self, fail_on=None):
        self.events = []
        self.fail_on = fail_on

    async def __call__(self, event, data):
        self.events.append((event, data))
        if event == self.fail_on:
            raise RuntimeError("emit boom")


def of_type(events, event):
    return [data for type_, data in events if type_ == event]


async def wait_event(rec, event, timeout=2.0):
    return await wait_for(lambda: any(type_ == event for type_, _ in rec.events), timeout)


async def run_sleep(ctx):
    await asyncio.sleep(3600)


async def test_crash_restarts_once(tmp_path):
    rec = Recorder()
    calls = []

    async def run_fail_once(ctx):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        await asyncio.sleep(3600)

    svc = InProcessService(
        ServiceSpec(name="svc", run=run_fail_once),
        make_ctx(tmp_path),
        emit=rec,
        restart_delay_s=0.01,
    )
    await svc.start()
    assert await wait_for(lambda: svc.restarts == 1, 2.0)
    assert svc.restarts == 1
    assert svc.started_at is not None
    assert of_type(rec.events, EVENT_SERVICE_STARTED)[0] == {"name": "svc", "pid": None}
    assert of_type(rec.events, EVENT_SERVICE_FAILED) == [{"name": "svc", "error": "boom"}]
    await svc.stop()


async def test_fenced_stops_without_restart(tmp_path):
    rec = Recorder()
    fenced = []
    calls = []

    async def run_fenced(ctx):
        calls.append(1)
        raise ServiceFenced

    svc = InProcessService(
        ServiceSpec(name="svc", run=run_fenced),
        make_ctx(tmp_path),
        emit=rec,
        restart_delay_s=0.01,
        on_fenced=fenced.append,
    )
    await svc.start()
    assert await wait_event(rec, EVENT_SERVICE_FENCED)
    assert svc.status == STOPPED
    assert svc.last_exit_code == FENCED_EXIT_CODE
    assert fenced == ["svc"]
    assert calls == [1]
    assert not svc.process_alive
    await svc.start()
    assert await wait_for(lambda: calls == [1, 1], 2.0)
    assert fenced == ["svc", "svc"]


async def test_normal_return_stops(tmp_path):
    rec = Recorder()
    calls = []

    async def run_ok(ctx):
        calls.append(1)

    svc = InProcessService(ServiceSpec(name="svc", run=run_ok), make_ctx(tmp_path), emit=rec)
    await svc.start()
    assert await wait_event(rec, EVENT_SERVICE_STOPPED)
    assert svc.status == STOPPED
    assert calls == [1]
    assert of_type(rec.events, EVENT_SERVICE_FAILED) == []
    assert not svc.process_alive
    await svc.start()
    assert calls == [1]


async def test_stop_cancels_task(tmp_path):
    rec = Recorder()

    svc = InProcessService(
        ServiceSpec(name="svc", run=run_sleep),
        make_ctx(tmp_path),
        emit=rec,
        restart_delay_s=0.01,
        stop_timeout_s=0.5,
    )
    await svc.start()
    assert await wait_event(rec, EVENT_SERVICE_STARTED)
    assert svc.status == RUNNING
    assert svc.process_alive
    await svc.stop()
    assert svc.status == STOPPED
    assert not svc.process_alive
    assert of_type(rec.events, EVENT_SERVICE_STOPPED) == [{"name": "svc"}]


async def test_emit_failure_does_not_break_restart(tmp_path):
    rec = Recorder(fail_on=EVENT_SERVICE_FAILED)
    calls = []

    async def run_fail(ctx):
        calls.append(1)
        raise RuntimeError("boom")

    svc = InProcessService(
        ServiceSpec(name="svc", run=run_fail),
        make_ctx(tmp_path),
        emit=rec,
        restart_delay_s=0.01,
    )
    await svc.start()
    assert await wait_for(lambda: svc.restarts >= 2, 2.0)
    assert svc.restarts >= 2
    await svc.stop()


async def test_target_routes_get_state(tmp_path):
    rec = Recorder()
    svc = InProcessService(
        ServiceSpec(name="svc", run=run_sleep),
        make_ctx(tmp_path),
        emit=rec,
        restart_delay_s=0.01,
    )
    await svc.start()
    assert await wait_for(lambda: svc.process_alive, 2.0)
    handler = StubService(node="node-a", service="svc", busy=True, working=False, state={"x": 1})
    target = InProcessTarget(svc, handler)
    assert target.name == "svc"
    assert target.alive
    assert target.busy is True
    assert target.working is False
    router = NodeRouter("node-a", peers={}, local_services={"svc": target})
    request = make_request(MSG_GET_STATE, dst=Address(node="node-a", service="svc"))
    response = await router.route(request)
    assert response.payload == await handler.get_state()
    await svc.stop()


async def test_target_unknown_action(tmp_path):
    svc = InProcessService(
        ServiceSpec(name="svc", run=run_sleep),
        make_ctx(tmp_path),
        emit=Recorder(),
        restart_delay_s=0.01,
    )
    target = InProcessTarget(svc, StubService())
    with pytest.raises(ProtoError) as exc:
        await target.forward(make_request(MSG_COMMAND, {"action": "nope", "args": {}}))
    assert exc.value.code == ERR_UNKNOWN_ACTION
    assert "nope" in exc.value.message
