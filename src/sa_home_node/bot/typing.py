from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

log = logging.getLogger(__name__)

TYPING_KEEPALIVE_INTERVAL_S = 4.0
TYPING_MAX_TICKS = 450


class TypingIndicator:
    def __init__(self, bot: Bot, chat_id: int, message_thread_id: int | None = None) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._message_thread_id = message_thread_id
        self._task: asyncio.Task[None] | None = None

    async def _send(self) -> None:
        try:
            await self._bot.send_chat_action(
                chat_id=self._chat_id, action="typing", message_thread_id=self._message_thread_id
            )
        except TelegramAPIError as exc:
            log.warning("Не удалось отправить typing в чат %s: %s", self._chat_id, exc)

    async def _loop(self) -> None:
        for _ in range(TYPING_MAX_TICKS):
            await asyncio.sleep(TYPING_KEEPALIVE_INTERVAL_S)
            await self._send()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            await self._send()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task = self._task
        if task is not None:
            self._task = None
            task.cancel()


@asynccontextmanager
async def typing_action(bot: Bot, chat_id: int, thread: int | None = None) -> AsyncIterator[TypingIndicator]:
    indicator = TypingIndicator(bot, chat_id, thread)
    await indicator.start()
    try:
        yield indicator
    finally:
        await indicator.stop()
