from __future__ import annotations

from typing import Any

from sa_home_node.proto.wire import ServiceUnavailableError

STATE = "get_state"
DESCRIBE = "describe"
ACTIONS = "actions"


class FakeLink:
    def __init__(self, replies: dict[tuple[Any, str], Any] | None = None, *, connected: bool = True) -> None:
        self.replies: dict[tuple[Any, str], Any] = dict(replies or {})
        self.connected = connected
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _reply(self, dst: Any, action: str) -> Any:
        if not self.connected:
            raise ServiceUnavailableError("нет соединения")
        if (dst, action) in self.replies:
            reply = self.replies[(dst, action)]
        elif (None, action) in self.replies:
            reply = self.replies[(None, action)]
        else:
            raise ServiceUnavailableError(f"нет заготовленного ответа: {dst}, {action}")
        if isinstance(reply, BaseException):
            raise reply
        return reply

    async def get_state(self, dst: Any = None) -> dict[str, Any]:
        self.calls.append((STATE, {"dst": dst}))
        return self._reply(dst, STATE)

    async def command(
        self,
        action: str,
        args: dict[str, Any] | None = None,
        dst: Any = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append((action, {"args": args, "dst": dst, "timeout": timeout}))
        return self._reply(dst, action)

    async def describe(self, dst: Any = None) -> Any:
        self.calls.append((DESCRIBE, {"dst": dst}))
        if not self.connected:
            return None
        return self._reply(dst, DESCRIBE)

    async def actions(self) -> tuple[Any, ...]:
        self.calls.append((ACTIONS, {}))
        if not self.connected:
            return ()
        return tuple(self._reply(None, ACTIONS))
