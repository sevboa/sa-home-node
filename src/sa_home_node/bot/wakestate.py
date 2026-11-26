from __future__ import annotations

import json
from typing import Any

from sa_home_node.bot.db import AppState
from sa_home_node.wake.peers import WAKE_KEY_PREFIX


class StateWakeCache:
    def __init__(self, state: AppState) -> None:
        self._state = state

    async def get(self, node: str) -> dict[str, Any] | None:
        raw = await self._state.get(f"{WAKE_KEY_PREFIX}{node}")
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) and data.get("mac") else None

    async def put(self, node: str, wake: dict[str, Any] | None) -> None:
        if not wake or not wake.get("mac"):
            return
        await self._state.set(f"{WAKE_KEY_PREFIX}{node}", json.dumps(wake))
