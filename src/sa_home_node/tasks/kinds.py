from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sa_home_node.proto.wire import Address
from sa_home_node.tasks.protocol import ACTION_TIMER

EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


def never(exc: Exception) -> bool:
    return False


@dataclass(frozen=True)
class TaskCall:
    task_id: int
    dst: Address
    args: dict[str, Any]
    meta: dict[str, Any]
    timeout_s: float
    deadline: float
    link: Any
    emit: EventEmitter


@dataclass(frozen=True)
class TaskKind:
    action: str
    execute: Callable[[TaskCall], Awaitable[dict[str, Any]]]
    wait_ready: bool = False
    prewake: bool = True
    retry: Callable[[Exception], bool] = field(default=never)


async def _timer(call: TaskCall) -> dict[str, Any]:
    return {}


TIMER = TaskKind(ACTION_TIMER, execute=_timer, prewake=False)
