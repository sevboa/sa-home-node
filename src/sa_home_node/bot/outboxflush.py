from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, ReplyParameters

from sa_home_node.bot.notifier import Notifier
from sa_home_node.bot.send import MAX_LEN, is_permanent_send_error
from sa_home_node.bot.store_outbox import OutboxStore

log = logging.getLogger(__name__)

LATE_MARK_AFTER = timedelta(seconds=60)
MAX_RETRY_AFTER = 3

_flush_lock = asyncio.Lock()


def _with_late_mark(text: str, created_at: datetime) -> str:
    mark = f"⏳ с задержкой, {created_at.astimezone().strftime('%H:%M')}"
    if len(text) + len(mark) + 2 > MAX_LEN:
        return text
    return f"{text}\n\n{mark}"


async def flush_outbox(
    notifier: Notifier,
    outbox: OutboxStore,
    *,
    now: Callable[[], datetime] | datetime | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    if _flush_lock.locked():
        return 0
    async with _flush_lock:
        current = now() if callable(now) else (now or datetime.now(UTC))
        expired = await outbox.expire(current)
        if expired:
            log.info("outbox: протухло и удалено записей: %s", expired)
        sent = 0
        for row in await outbox.due(current):
            payload = row["payload"]
            text = payload.get("text", "")
            if (current - row["created_at"]) > LATE_MARK_AFTER:
                text = _with_late_mark(text, row["created_at"])
            reply = (
                ReplyParameters(message_id=payload["reply_to_message_id"], allow_sending_without_reply=True)
                if payload.get("reply_to_message_id") is not None
                else None
            )
            markup = (
                InlineKeyboardMarkup.model_validate(payload["reply_markup"])
                if payload.get("reply_markup")
                else None
            )
            retries = 0
            while True:
                try:
                    await notifier.bot.send_message(
                        row["chat_id"],
                        text,
                        reply_parameters=reply,
                        reply_markup=markup,
                        message_thread_id=row["message_thread_id"],
                    )
                except TelegramRetryAfter as exc:
                    retries += 1
                    if retries > MAX_RETRY_AFTER:
                        return sent
                    wait = exc.retry_after + 1
                    log.warning("outbox: 429 от Telegram, жду %ss", wait)
                    await sleep(wait)
                    continue
                except Exception as exc:  # noqa: BLE001
                    if is_permanent_send_error(exc):
                        log.warning(
                            "outbox: запись %s (chat=%s) не доставить, удаляю: %s",
                            row["id"],
                            row["chat_id"],
                            exc,
                        )
                        await outbox.delete(row["id"])
                        break
                    log.warning("outbox: связи нет, флаш прерван (запись %s): %s", row["id"], exc)
                    await outbox.bump_attempts(row["id"])
                    return sent
                await outbox.delete(row["id"])
                sent += 1
                break
        return sent


class OutboxFlusher:
    def __init__(self, notifier: Notifier, outbox: OutboxStore) -> None:
        self._notifier = notifier
        self._outbox = outbox
        self._task: asyncio.Task[None] | None = None
        self._kicks: set[asyncio.Task[None]] = set()

    async def flush(self) -> None:
        try:
            sent = await flush_outbox(self._notifier, self._outbox)
        except Exception:
            log.warning("outbox: флаш упал", exc_info=True)
            return
        if sent:
            log.info("outbox: доставлено из очереди %d", sent)

    def kick(self) -> None:
        task = asyncio.create_task(self.flush())
        self._kicks.add(task)
        task.add_done_callback(self._kicks.discard)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(60.0)
            await self.flush()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="outbox-flush")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
