from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter

log = logging.getLogger(__name__)

T = TypeVar("T")

REQUEST_TIMEOUT_S = 10.0


async def call_with_network_retry(
    factory: Callable[[], Awaitable[T]], *, what: str, attempts: int = 3, delay_s: float = 5.0
) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return await factory()
        except TelegramRetryAfter as exc:
            if attempt >= attempts:
                raise
            log.warning(
                "%s: Telegram просит подождать %sс (попытка %d/%d)", what, exc.retry_after, attempt, attempts
            )
            await asyncio.sleep(exc.retry_after + 1)
        except TelegramNetworkError as exc:
            if attempt >= attempts:
                raise
            log.warning(
                "%s: сетевая ошибка (%s) — повтор через %.0fс (попытка %d/%d)",
                what,
                exc,
                delay_s,
                attempt,
                attempts,
            )
            await asyncio.sleep(delay_s)
    raise ValueError(attempts)
