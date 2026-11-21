import asyncio
import os
from pathlib import Path

import pytest
from pydantic import BaseModel
from support import wait_for

from sa_home_node import node
from sa_home_node.config import ConfigError, load_config
from sa_home_node.content import ContentError, ContentStore
from sa_home_node.plugins import (
    Plugin,
    PluginError,
    ServiceContext,
    ServiceFenced,
    ServiceSpec,
    make_context,
)
from sa_home_node.proto.actions import NodeAction
from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.describe import ActionParam, ActionSpec
from sa_home_node.proto.wire import Address
from sa_home_node.services.assignments import parse
from sa_home_node.services.inproc import InProcessService
from sa_home_node.services.process import ProcessService
from sa_home_node.services.registry import ServiceTable
from sa_home_node.services.supervisor import process_argv


class NeedField(BaseModel):
    required: int


def write_config(tmp_path, sock_dir, plugins, assignments=()):
    path = tmp_path / "config.toml"
    listed = ", ".join(f'"{p}"' for p in plugins)
    assigned = ", ".join(f'"{a}"' for a in assignments)
    path.write_text(
        f'[node]\nid = "n"\nsocket = "{sock_dir}/node.sock"\n'
        f'state_path = "{tmp_path}/node-state.json"\nplugins = [{listed}]\n'
        f"assignments = [{assigned}]\n"
    )
    return path


def make_plugin(name, run, keys=()):
    return Plugin(name=name, services=(ServiceSpec(f"{name}-s", run),), content_keys=keys)


@pytest.fixture(autouse=True)
def hard_exits(monkeypatch):
    armed = []
    monkeypatch.setattr(node, "arm_hard_exit", armed.append)
    return armed


async def _noop_svc(ctx: ServiceContext) -> None:
    return None


def make_service(name, started, cancelled, ev):
    async def svc(ctx: ServiceContext) -> None:
        started.append(name)
        ev.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(name)

    return svc


async def test_start_and_cancel_order(tmp_path, monkeypatch, sock_dir):
    monkeypatch.chdir(tmp_path)
    cfg = write_config(tmp_path, sock_dir, ["p1", "p2"], ["p1-s", "p2-s"])
    started, cancelled = [], []
    started_evs = []
    plugins = []
    for name in ("p1", "p2"):
        ev = asyncio.Event()
        started_evs.append(ev)
        plugins.append(make_plugin(name, make_service(name, started, cancelled, ev)))
    monkeypatch.setattr(node, "load_plugins", lambda names: plugins)
    stop = asyncio.Event()
    task = asyncio.create_task(node.run_node(cfg, stop=stop))
    for ev in started_evs:
        await ev.wait()
    assert started == ["p1", "p2"]
    for name in ("p1", "p2"):
        assert (tmp_path / "data" / name).is_dir()
    stop.set()
    await asyncio.gather(task, return_exceptions=True)
    assert cancelled == ["p2", "p1"]
    assert await task is False


async def test_only_assigned_services_start(tmp_path, monkeypatch, sock_dir, hard_exits):
    monkeypatch.chdir(tmp_path)
    cfg = write_config(tmp_path, sock_dir, ["p1", "p2"], ["p1-s", "ghost"])
    started, cancelled = [], []
    ev = asyncio.Event()
    plugins = [
        make_plugin("p1", make_service("p1", started, cancelled, ev)),
        make_plugin("p2", make_service("p2", started, cancelled, asyncio.Event())),
    ]
    monkeypatch.setattr(node, "load_plugins", lambda names: plugins)
    stop = asyncio.Event()
    task = asyncio.create_task(node.run_node(cfg, stop=stop))
    await ev.wait()
    await asyncio.sleep(0.1)
    assert started == ["p1"]
    stop.set()
    assert await asyncio.wait_for(task, timeout=10) is False
    assert hard_exits == [90.0 + 20.0 + 15.0]


async def test_state_assignment_starts_service(tmp_path, monkeypatch, sock_dir):
    monkeypatch.chdir(tmp_path)
    cfg = write_config(tmp_path, sock_dir, ["p1"])
    (tmp_path / "node-state.json").write_text('{"assignments": ["p1-s"]}')
    started = []
    ev = asyncio.Event()
    monkeypatch.setattr(
        node, "load_plugins", lambda names: [make_plugin("p1", make_service("p1", started, [], ev))]
    )
    stop = asyncio.Event()
    task = asyncio.create_task(node.run_node(cfg, stop=stop))
    await asyncio.wait_for(ev.wait(), timeout=5)
    stop.set()
    await asyncio.wait_for(task, timeout=10)


async def test_instance_service_gets_package_path(tmp_path, monkeypatch, sock_dir):
    monkeypatch.chdir(tmp_path)
    cfg = write_config(tmp_path, sock_dir, ["p1"], ["p1-s@one"])
    ctxs = []
    ev = asyncio.Event()

    async def svc(ctx: ServiceContext) -> None:
        ctxs.append(ctx)
        ev.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(node, "load_plugins", lambda names: [make_plugin("p1", svc)])
    stop = asyncio.Event()
    task = asyncio.create_task(node.run_node(cfg, stop=stop))
    await asyncio.wait_for(ev.wait(), timeout=5)
    stop.set()
    await asyncio.wait_for(task, timeout=10)
    assert ctxs[0].instance == "one"
    assert ctxs[0].package_path == tmp_path / "instances" / "p1-s.one.toml"


async def test_restart_node_returns_true(tmp_path, monkeypatch, sock_dir):
    monkeypatch.chdir(tmp_path)
    cfg = write_config(tmp_path, sock_dir, ["p1"], ["p1-s"])
    ev = asyncio.Event()
    monkeypatch.setattr(
        node, "load_plugins", lambda names: [make_plugin("p1", make_service("p1", [], [], ev))]
    )
    task = asyncio.create_task(node.run_node(cfg, stop=asyncio.Event()))
    sock = Path(sock_dir) / "node.sock"
    assert await wait_for(lambda: os.path.exists(sock), timeout=5)
    client = ProtoClient(sock)
    await client.connect()
    try:
        await client.command("restart_node", {}, dst=Address(node="n", service="node"))
        assert await asyncio.wait_for(task, timeout=15) is True
    finally:
        await client.close()


async def test_restarts_failed_service(tmp_path, monkeypatch, sock_dir):
    monkeypatch.chdir(tmp_path)
    cfg = write_config(tmp_path, sock_dir, ["p1"], ["p1-s"])
    runs = []
    stop = asyncio.Event()

    async def svc(ctx: ServiceContext) -> None:
        runs.append(True)
        if len(runs) < 2:
            raise RuntimeError("упала")
        stop.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(node, "load_plugins", lambda names: [make_plugin("p1", svc)])
    await node.run_node(cfg, stop=stop, restart_delay_s=0.0)
    assert len(runs) >= 2


@pytest.mark.parametrize("kind", ["section", "content"])
async def test_error_before_services_start(tmp_path, monkeypatch, sock_dir, kind):
    started = []

    async def svc(ctx: ServiceContext) -> None:
        started.append(True)

    if kind == "section":
        plugin = Plugin(name="p1", config_model=NeedField, services=(ServiceSpec("s", svc),))
        raises, match = ConfigError, "p1"
    else:
        plugin = Plugin(name="p1", services=(ServiceSpec("s", svc),), content_keys=("p1.k",))
        raises, match = ContentError, "не найден"
    cfg = write_config(tmp_path, sock_dir, ["p1"], ["s"])
    monkeypatch.setattr(node, "load_plugins", lambda names: [plugin])
    with pytest.raises(raises, match=match):
        await node.run_node(cfg, stop=asyncio.Event())
    assert not started


async def test_node_with_swarm_api_and_node_actions(tmp_path, monkeypatch, sock_dir):
    monkeypatch.chdir(tmp_path)
    cfg = write_config(tmp_path, sock_dir, ["p1"], ["svc"])
    node_ids = []
    stop = asyncio.Event()

    async def ping(args):
        return {"pong": args.get("text", "")}

    async def svc(ctx: ServiceContext) -> None:
        node_ids.append(ctx.swarm.node_id)
        while True:
            await ctx.swarm.emit("test_greeting", {"text": "привет"})
            await asyncio.sleep(0.05)

    plugin = Plugin(
        name="p1",
        services=(ServiceSpec("svc", svc),),
        node_actions=(
            NodeAction(
                ActionSpec(id="p1_ping", title="Пинг p1", params=(ActionParam(name="text"),)), ping
            ),
        ),
    )
    monkeypatch.setattr(node, "load_plugins", lambda names: [plugin])
    seen = []

    async def on_event(env):
        seen.append(env)

    task = asyncio.create_task(node.run_node(cfg, stop=stop))
    client = None
    try:
        assert await wait_for(lambda: bool(node_ids), timeout=5)
        assert node_ids == ["n"]
        sock = Path(sock_dir) / "node.sock"
        assert await wait_for(lambda: os.path.exists(sock), timeout=5)
        client = ProtoClient(sock, on_event=on_event)
        await client.connect()
        assert await wait_for(
            lambda: any(e.payload.get("event") == "test_greeting" for e in seen), timeout=5
        )
        desc = await client.describe(dst=Address(node="n", service="node"))
        assert any(a.id == "p1_ping" for a in desc.actions)
        result = await client.command(
            "p1_ping", {"text": "hi"}, dst=Address(node="n", service="node")
        )
        assert result == {"pong": "hi"}
        greeting = next(e for e in seen if e.payload.get("event") == "test_greeting")
        assert greeting.src is not None
        assert greeting.src.node == "n"
    finally:
        if client is not None:
            await client.close()
        stop.set()
        await asyncio.wait_for(task, timeout=10)


def test_slot_factory_picks_process_or_embedded(tmp_path, sock_dir):
    cfg = load_config(write_config(tmp_path, sock_dir, ["p1"]))
    plugin = Plugin(
        name="p1",
        services=(ServiceSpec("proc", _noop_svc, process=True), ServiceSpec("emb", _noop_svc)),
    )
    table = ServiceTable([plugin])
    contexts = {"p1": make_context(plugin, cfg, ContentStore(cfg.content_dir))}
    store = node.instance_store(cfg)
    factory = node.make_slot_factory(
        cfg, table, contexts, store, emit=_emit, restart_delay_s=1.0,
        on_fenced=lambda name: None, on_spawned=lambda name: None,
    )
    proc = factory(table.spec("proc"), parse("proc@one"))
    emb = factory(table.spec("emb"), parse("emb"))
    assert isinstance(proc, ProcessService)
    assert proc.name == "proc@one"
    assert proc._argv == process_argv("proc", "one", str(cfg.path))
    assert isinstance(emb, InProcessService)
    assert emb.ctx.package_path is None


async def _emit(event, data):
    return None


async def test_run_service_unknown(tmp_path, monkeypatch, sock_dir):
    cfg = write_config(tmp_path, sock_dir, ["p1"])
    monkeypatch.setattr(node, "load_plugins", lambda names: [make_plugin("p1", _noop_svc)])
    with pytest.raises(PluginError, match="нет процессной службы: hello"):
        await node.run_service("hello", config_path=cfg, stop=asyncio.Event())


def patch_service(monkeypatch, tmp_path, sock_dir, run):
    monkeypatch.chdir(tmp_path)
    plugin = make_plugin("p1", _noop_svc)
    monkeypatch.setattr(node, "load_plugins", lambda names: [plugin])
    monkeypatch.setattr(
        node.ServiceTable, "spec", lambda self, name: ServiceSpec(name="hello", run=run, process=True)
    )
    monkeypatch.setattr(node.ServiceTable, "plugin_of", lambda self, name: plugin)
    return write_config(tmp_path, sock_dir, ["p1"])


async def test_run_service_fenced(tmp_path, monkeypatch, sock_dir):
    ctxs = []

    async def svc(ctx: ServiceContext) -> None:
        ctxs.append(ctx)
        raise ServiceFenced("вытеснена")

    cfg = patch_service(monkeypatch, tmp_path, sock_dir, svc)
    assert await node.run_service("hello", instance="one", config_path=cfg, stop=asyncio.Event()) == 11
    assert ctxs[0].instance == "one"
    assert ctxs[0].package_path == tmp_path / "instances" / "hello.one.toml"
    assert (tmp_path / "data" / "p1").is_dir()


async def test_run_service_returns_zero(tmp_path, monkeypatch, sock_dir):
    cfg = patch_service(monkeypatch, tmp_path, sock_dir, _noop_svc)
    assert await node.run_service("hello", config_path=cfg, stop=asyncio.Event()) == 0


async def test_run_service_stops(tmp_path, monkeypatch, sock_dir):
    started = []

    async def svc(ctx: ServiceContext) -> None:
        started.append(True)
        await asyncio.Event().wait()

    cfg = patch_service(monkeypatch, tmp_path, sock_dir, svc)
    stop = asyncio.Event()
    task = asyncio.create_task(node.run_service("hello", config_path=cfg, stop=stop))
    await wait_for(lambda: bool(started), timeout=5)
    stop.set()
    assert await asyncio.wait_for(task, timeout=10) == 0
