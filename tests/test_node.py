import asyncio

import pytest
from pydantic import BaseModel

from sa_home_node import node
from sa_home_node.config import ConfigError
from sa_home_node.content import ContentError
from sa_home_node.plugins import Plugin, ServiceContext, ServiceSpec


class NeedField(BaseModel):
    required: int


def write_config(tmp_path, plugins):
    path = tmp_path / "config.toml"
    listed = ", ".join(f'"{p}"' for p in plugins)
    path.write_text(f'[node]\nid = "n"\nplugins = [{listed}]\n')
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


async def test_start_and_cancel_order(tmp_path, monkeypatch):
    cfg = write_config(tmp_path, ["p1", "p2"])
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


async def test_restarts_failed_service(tmp_path, monkeypatch):
    cfg = write_config(tmp_path, ["p1"])
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
async def test_error_before_services_start(tmp_path, monkeypatch, kind):
    cfg = write_config(tmp_path, ["p1"])
    started = []

    async def svc(ctx: ServiceContext) -> None:
        started.append(True)

    if kind == "section":
        plugin = Plugin(name="p1", config_model=NeedField, services=(ServiceSpec("s", svc),))
        raises, match = ConfigError, "p1"
    else:
        plugin = Plugin(name="p1", services=(ServiceSpec("s", svc),), content_keys=("p1.k",))
        raises, match = ContentError, "не найден"
    monkeypatch.setattr(node, "load_plugins", lambda names: [plugin])
    with pytest.raises(raises, match=match):
        await node.run_node(cfg, stop=asyncio.Event())
    assert not started
