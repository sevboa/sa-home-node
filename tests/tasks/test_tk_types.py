import asyncio
import dataclasses
from pathlib import Path

import pytest

from sa_home_node.tasks import protocol
from sa_home_node.tasks.config import TasksConfig
from sa_home_node.tasks.kinds import TIMER, TaskCall, TaskKind, never


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SERVICE_NAME", "tasks"),
        ("ACTION_CREATE", "create"),
        ("ACTION_TIMER", "timer"),
        ("ACTION_FIRE_NOW", "fire_now"),
        ("ACTION_MATCH_EVENT", "match_event"),
        ("EVENT_TASK_PREWAKE", "task_prewake"),
        ("EVENT_TASK_RESULT", "task_result"),
        ("PREWAKE_LEAD_S", 600.0),
        ("POLL_INTERVAL_S", 30.0),
        ("FIRE_GRACE_S", 300.0),
        ("FIRE_RETRY_INTERVAL_S", 30.0),
    ],
)
def test_protocol_values(name, value):
    assert getattr(protocol, name) == value


def test_config_defaults():
    cfg = TasksConfig()
    assert cfg.socket == "./data/tasks.sock"
    assert cfg.db_path == Path("./data/tasks.sqlite")
    assert cfg.default_timeout_s == 240.0
    assert cfg.warmup_timeout_s == 360.0


@pytest.mark.parametrize("field", ["default_timeout_s", "warmup_timeout_s"])
@pytest.mark.parametrize("value", [0, -1.0])
def test_config_rejects_non_positive(field, value):
    with pytest.raises(ValueError):
        TasksConfig(**{field: value})


async def _emit(name, payload):
    return None


def test_timer_returns_empty_and_skips_prewake():
    call = TaskCall(1, "node-a", {}, {}, 1.0, 2.0, None, _emit)
    assert asyncio.run(TIMER.execute(call)) == {}
    assert TIMER.action == protocol.ACTION_TIMER
    assert TIMER.prewake is False
    assert TIMER.wait_ready is False


def test_kind_defaults_and_frozen():
    kind = TaskKind("act", execute=TIMER.execute)
    assert kind.prewake is True
    assert kind.retry is never
    assert never(RuntimeError()) is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        kind.action = "other"
