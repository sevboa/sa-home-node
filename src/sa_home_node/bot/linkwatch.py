from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import TelegramMethod
from aiogram.methods.base import Response, TelegramType

log = logging.getLogger(__name__)

OFFLINE_THRESHOLD_S = 60.0

OnReconnect = Callable[[float], Awaitable[None]]


class LinkWatchMiddleware(BaseRequestMiddleware):
    def __init__(
        self,
        on_reconnect: OnReconnect,
        *,
        threshold_seconds: float = OFFLINE_THRESHOLD_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_reconnect = on_reconnect
        self._threshold = threshold_seconds
        self._clock = clock
        self._down_since: float | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        try:
            result = await make_request(bot, method)
        except TelegramNetworkError:
            if self._down_since is None:
                self._down_since = self._clock()
                log.warning("Потеря связи с Telegram")
            raise
        if self._down_since is not None:
            downtime = self._clock() - self._down_since
            self._down_since = None
            log.info("Связь с Telegram восстановлена (офлайн %.0fс)", downtime)
            if downtime >= self._threshold:
                task = asyncio.create_task(self._notify(downtime))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        return result

    async def _notify(self, downtime: float) -> None:
        try:
            await self._on_reconnect(downtime)
        except Exception as exc:  # noqa: BLE001
            log.warning("Обработчик восстановления связи упал: %s", exc)
