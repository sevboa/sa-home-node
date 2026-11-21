from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sa_home_node.proto.describe import ActionSpec
from sa_home_node.proto.wire import ERR_UNKNOWN_ACTION, ProtoError

ActionHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class NodeAction:
    spec: ActionSpec
    handler: ActionHandler


class ActionTable:
    def __init__(self, order: Sequence[str] = ()) -> None:
        self._order = order
        self._specs: list[ActionSpec] = []
        self._handlers: dict[str, ActionHandler] = {}

    def add(self, spec: ActionSpec, handler: ActionHandler) -> None:
        if spec.id in self._handlers:
            raise ValueError(f"дубликат действия: {spec.id}")
        self._specs.append(spec)
        self._handlers[spec.id] = handler

    def specs(self) -> tuple[ActionSpec, ...]:
        by_id = {s.id: s for s in self._specs}
        ordered = tuple(by_id[i] for i in self._order if i in by_id)
        ordered += tuple(s for s in self._specs if s.id not in set(self._order))
        return ordered

    def __contains__(self, action_id: object) -> bool:
        return isinstance(action_id, str) and action_id in self._handlers

    async def run(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(action)
        if handler is None:
            raise ProtoError(ERR_UNKNOWN_ACTION, f"нет такого действия: {action}")
        return await handler(args)
