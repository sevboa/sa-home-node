import asyncio
import dataclasses

import pytest

from sa_home_node.config import load_config
from sa_home_node.content import ContentStore
from sa_home_node.monitor import run as run_module
from sa_home_node.monitor.metrics import METRICS
from sa_home_node.monitor.plugin import PLUGIN
from sa_home_node.monitor.service import ACTION_HOST_TREND
from sa_home_node.plugins import make_context


async def _broadcast(event_type, data):
    return 0


def _ctx(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        f'[node]\nid = "n1"\n\n[monitor]\ndb_path = "{tmp_path}/m.sqlite"\nsocket = "{tmp_path}/m.sock"\n'
    )
    (tmp_path / "content").mkdir()
    ctx = make_context(PLUGIN, load_config(path), ContentStore(tmp_path / "content"), plugins=[PLUGIN])
    return dataclasses.replace(ctx, handler=PLUGIN.services[0].handler(ctx), broadcast=_broadcast)


async def test_command_waits_until_monitor_started(tmp_path):
    ctx = _ctx(tmp_path)
    metric = next(iter(METRICS))
    pending = asyncio.create_task(ctx.handler.run_command(ACTION_HOST_TREND, {"metric": metric, "hours": 1}))
    await asyncio.sleep(0.05)
    assert not pending.done()
    task = asyncio.create_task(run_module.run_monitor(ctx))
    result = await asyncio.wait_for(pending, 2)
    assert result == {"metric": metric, "hours": 1, "buckets": result["buckets"]}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
