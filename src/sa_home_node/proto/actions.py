from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sa_home_node.proto.describe import ActionSpec
from sa_home_node.proto.wire import ERR_UNKNOWN_ACTION, ProtoError

ActionHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ActionTable:
    def __init__(self) -> None:
        self._specs: list[ActionSpec] = []
        self._handlers: dict[str, ActionHandler] = {}

    def add(self, spec: ActionSpec, handler: ActionHandler) -> None:
        if spec.id in self._handlers:
            raise ValueError(f"дубликат действия: {spec.id}")
        self._specs.append(spec)
        self._handlers[spec.id] = handler

    def specs(self) -> tuple[ActionSpec, ...]:
        return tuple(self._specs)

    def __contains__(self, action_id: object) -> bool:
        return isinstance(action_id, str) and action_id in self._handlers

    async def run(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(action)
        if handler is None:
            raise ProtoError(ERR_UNKNOWN_ACTION, f"нет такого действия: {action}")
        return await handler(args)
