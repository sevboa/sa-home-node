import asyncio
import os
from pathlib import Path

import pytest
from pydantic import BaseModel
from support import wait_for

from sa_home_node import node
from sa_home_node.config import ConfigError
from sa_home_node.content import ContentError
from sa_home_node.plugins import Plugin, ServiceContext, ServiceSpec
from sa_home_node.proto.actions import NodeAction
from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.describe import ActionParam, ActionSpec
from sa_home_node.proto.wire import Address


class NeedField(BaseModel):
    required: int


def write_config(tmp_path, sock_dir, plugins):
    path = tmp_path / "config.toml"
    listed = ", ".join(f'"{p}"' for p in plugins)
    path.write_text(
        f'[node]\nid = "n"\nsocket = "{sock_dir}/node.sock"\n'
        f'state_path = "{tmp_path}/node-state.json"\nplugins = [{listed}]\n'
    )
    return path


def make_plugin(name, run, keys=()):
    return Plugin(name=name, services=(ServiceSpec("s", run),), content_keys=keys)


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
    cfg = write_config(tmp_path, sock_dir, ["p1", "p2"])
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


async def test_restarts_failed_service(tmp_path, monkeypatch, sock_dir):
    monkeypatch.chdir(tmp_path)
    cfg = write_config(tmp_path, sock_dir, ["p1"])
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
    cfg = write_config(tmp_path, sock_dir, ["p1"])
    monkeypatch.setattr(node, "load_plugins", lambda names: [plugin])
    with pytest.raises(raises, match=match):
        await node.run_node(cfg, stop=asyncio.Event())
    assert not started


async def test_node_with_swarm_api_and_node_actions(tmp_path, monkeypatch, sock_dir):
    monkeypatch.chdir(tmp_path)
    cfg = write_config(tmp_path, sock_dir, ["p1"])
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
