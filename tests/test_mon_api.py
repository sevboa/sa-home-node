import asyncio

from pydantic import BaseModel

from sa_home_node import node
from sa_home_node.config import load_config
from sa_home_node.content import ContentStore
from sa_home_node.monitor.providers import SensorProvider
from sa_home_node.plugins import Plugin, ServiceContext, ServiceSpec, make_context


class Extra(BaseModel):
    level: int = 3


class Other(BaseModel):
    name: str = "x"


def write_cfg(tmp_path, body=""):
    path = tmp_path / "config.toml"
    path.write_text(f'[node]\nid = "n"\nkind = "vps"\n{body}')
    return path


def test_make_context_fills_extensions(tmp_path):
    cfg = load_config(write_cfg(tmp_path, "\n[extra]\nlevel = 7\n"))
    plugin = Plugin(name="p", sections=(("extra", Extra), ("other", Other)))
    other = Plugin(name="q")
    ctx = make_context(plugin, cfg, ContentStore(tmp_path), plugins=[plugin, other])
    assert ctx.node_kind == "vps"
    assert ctx.sections["extra"].level == 7
    assert ctx.sections["other"].name == "x"
    assert ctx.plugins == (plugin, other)
    assert ctx.broadcast is None
    assert ctx.handler is None


def test_make_context_defaults(tmp_path):
    cfg = load_config(write_cfg(tmp_path))
    ctx = make_context(Plugin(name="p"), cfg, ContentStore(tmp_path))
    assert dict(ctx.sections) == {}
    assert ctx.plugins == ()


def test_sensor_provider_protocol():
    class Fake:
        platform = "linux"

        def read_cpu(self, now):
            return []

        def read_disks(self, now):
            return []

        def read_disk_summaries(self):
            return []

        def problem(self):
            return None

    plugin = Plugin(name="p", sensor_providers=(lambda ctx: Fake(),))
    provider: SensorProvider = plugin.sensor_providers[0](None)
    assert provider.platform == "linux" and provider.problem() is None


async def test_run_service_passes_token_and_broadcast(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = {}

    class FakeServer:
        def __init__(self, endpoint, handler, *, token=""):
            seen.update(endpoint=endpoint, handler=handler, token=token)

        async def start(self):
            seen["started"] = True

        async def stop(self):
            seen["stopped"] = True

        async def broadcast_event(self, event_type, data=None):
            return 2

    async def run(ctx: ServiceContext) -> None:
        seen["ctx"] = ctx

    handler = object()
    spec = ServiceSpec(
        name="svc", run=run, process=True, handler=lambda ctx: handler, endpoint=lambda c: "./svc.sock"
    )
    plugin = Plugin(name="p", services=(spec,))
    monkeypatch.setattr(node, "load_plugins", lambda names: [plugin])
    monkeypatch.setattr(node, "ProtoServer", FakeServer)
    path = write_cfg(tmp_path, '\n[swarm]\ntoken = "tok"\n')
    assert await node.run_service("svc", config_path=path, stop=asyncio.Event()) == 0
    ctx = seen["ctx"]
    assert seen["token"] == "tok"
    assert seen["handler"] is handler and ctx.handler is handler
    assert await ctx.broadcast("e", {}) == 2
    assert ctx.plugins == (plugin,)
    assert ctx.node_kind == "vps"
    assert seen["stopped"]
