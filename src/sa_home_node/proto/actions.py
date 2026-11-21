from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sa_home_node.proto.describe import ActionSpec
from sa_home_node.proto.wire import ERR_UNKNOWN_ACTION, ProtoError

ActionHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
SpecSource = ActionSpec | Callable[[], ActionSpec]


@dataclass(frozen=True)
class NodeAction:
    spec: ActionSpec
    handler: ActionHandler


def resolve(source: SpecSource) -> ActionSpec:
    return source if isinstance(source, ActionSpec) else source()


class ActionTable:
    def __init__(self, order: Sequence[str] = ()) -> None:
        self._order = order
        self._sources: list[SpecSource] = []
        self._handlers: dict[str, ActionHandler] = {}

    def add(self, spec: SpecSource, handler: ActionHandler) -> None:
        self.add_hidden(resolve(spec).id, handler)
        self._sources.append(spec)

    def add_hidden(self, action_id: str, handler: ActionHandler) -> None:
        if action_id in self._handlers:
            raise ValueError(f"дубликат действия: {action_id}")
        self._handlers[action_id] = handler

    def specs(self) -> tuple[ActionSpec, ...]:
        resolved = [resolve(source) for source in self._sources]
        by_id = {s.id: s for s in resolved}
        ordered = tuple(by_id[i] for i in self._order if i in by_id)
        ordered += tuple(s for s in resolved if s.id not in set(self._order))
        return ordered

    def __contains__(self, action_id: object) -> bool:
        return isinstance(action_id, str) and action_id in self._handlers

    async def run(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(action)
        if handler is None:
            raise ProtoError(ERR_UNKNOWN_ACTION, f"нет такого действия: {action}")
        return await handler(args)
