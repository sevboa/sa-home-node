import asyncio
import logging

import pytest

from sa_home_node.content import ContentStore
from sa_home_node.example import PLUGIN, ExampleConfig
from sa_home_node.plugins import ServiceContext


async def test_hello_logs_and_cancels(caplog, tmp_path):
    (tmp_path / "example.toml").write_text('greeting = "Привет"\n')
    ctx = ServiceContext(
        node_id="n",
        data_dir=tmp_path,
        config=ExampleConfig(),
        content=ContentStore(tmp_path),
    )
    assert PLUGIN.name == "example"
    assert PLUGIN.content_keys == ("example.greeting",)
    assert ExampleConfig().interval_s == 60.0
    caplog.set_level(logging.INFO)
    task = asyncio.create_task(PLUGIN.services[0].run(ctx))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "Привет" in caplog.text
