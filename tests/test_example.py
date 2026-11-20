import asyncio
import logging
from types import SimpleNamespace

import pytest

from sa_home_node.content import ContentStore
from sa_home_node.example import PLUGIN, ExampleConfig, example_ping, hello
from sa_home_node.plugins import ServiceContext


def make_ctx(tmp_path, config=None, swarm=None):
    (tmp_path / "example.toml").write_text('greeting = "Привет"\n')
    return ServiceContext(
        node_id="n",
        data_dir=tmp_path,
        config=config or ExampleConfig(),
        content=ContentStore(tmp_path),
        swarm=swarm,
    )


async def test_hello_logs_and_cancels(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    ctx = make_ctx(tmp_path)
    assert PLUGIN.name == "example"
    assert PLUGIN.content_keys == ("example.greeting",)
    assert ExampleConfig().interval_s == 60.0
    task = asyncio.create_task(PLUGIN.services[0].run(ctx))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "Привет" in caplog.text


async def test_hello_emits_greeting(tmp_path):
    sent = []

    async def fake_emit(event, data, *, service="node"):
        sent.append((event, data, service))

    ctx = make_ctx(
        tmp_path, config=ExampleConfig(interval_s=0.01), swarm=SimpleNamespace(emit=fake_emit)
    )
    task = asyncio.create_task(hello(ctx))
    while not sent:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sent == [("example_greeting", {"text": "Привет"}, "node")]


async def test_example_ping_action():
    action = next(a for a in PLUGIN.node_actions if a.spec.id == "example_ping")
    assert example_ping is action.handler
    assert await action.handler({"text": "x"}) == {"pong": "x"}
    assert await action.handler({}) == {"pong": ""}
