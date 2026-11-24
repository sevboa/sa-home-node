from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNotFound,
    TelegramRetryAfter,
)

log = logging.getLogger(__name__)

T = TypeVar("T")

MAX_LEN = 4096
MAX_RETRIES = 3
TRANSIENT_BACKOFF_S = (0.0, 2.0, 5.0)
PARSE_ERROR_MARK = "can't parse entities"


def is_parse_error(exc: BaseException) -> bool:
    return isinstance(exc, TelegramBadRequest) and PARSE_ERROR_MARK in str(exc)


def is_permanent_send_error(exc: BaseException) -> bool:
    return isinstance(exc, (TelegramBadRequest, TelegramForbiddenError, TelegramNotFound))


async def send_with_retry(
    factory: Callable[[], Awaitable[T]], *, what: str = "сообщение", outcome: dict[str, bool] | None = None
) -> T | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await factory()
        except TelegramRetryAfter as exc:
            log.warning("429 от Telegram (%s), жду %ss", what, exc.retry_after + 1)
            await asyncio.sleep(exc.retry_after + 1)
        except Exception as exc:  # noqa: BLE001
            if is_permanent_send_error(exc):
                log.warning("Не удалось отправить %s (перманентный сбой, без ретрая): %s", what, exc)
                if outcome is not None and is_parse_error(exc):
                    outcome["parse_error"] = True
                return None
            log.warning("Не удалось отправить %s (попытка %s/%s): %s", what, attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(TRANSIENT_BACKOFF_S[attempt - 1])
    if outcome is not None:
        outcome["transient"] = True
    return None


def chunk_text(text: str, limit: int = MAX_LEN) -> list[str]:
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest or not chunks:
        chunks.append(rest)
    return chunks
