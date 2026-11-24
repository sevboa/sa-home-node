from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.types import ReplyParameters

from sa_home_node.bot.attach import AttachMixin
from sa_home_node.bot.send import chunk_text, send_with_retry
from sa_home_node.bot.store_outbox import OUTBOX_KIND_INTERACTIVE, OutboxStore


class Notifier(AttachMixin):
    def __init__(self, bot: Bot, outbox: OutboxStore | None = None) -> None:
        self.bot = bot
        self._outbox = outbox

    async def _send_chunk(
        self,
        chat_id: int,
        chunk: str,
        reply_parameters: ReplyParameters | None,
        reply_markup: Any,
        thread: int | None,
        outcome: dict[str, bool],
    ) -> Any:
        def factory(**plain: Any) -> Callable[[], Awaitable[Any]]:
            return lambda: self.bot.send_message(
                chat_id,
                chunk,
                reply_parameters=reply_parameters,
                reply_markup=reply_markup,
                message_thread_id=thread,
                **plain,
            )

        msg = await send_with_retry(factory(), outcome=outcome)
        if msg is None and outcome.get("parse_error"):
            msg = await send_with_retry(factory(parse_mode=None), outcome=outcome)
        return msg

    async def send_direct(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: Any = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        outbox_kind: str = OUTBOX_KIND_INTERACTIVE,
        dedup_key: str | None = None,
    ) -> int | None:
        chunks = chunk_text(text)
        reply_parameters = (
            None
            if reply_to_message_id is None
            else ReplyParameters(message_id=reply_to_message_id, allow_sending_without_reply=True)
        )
        delivered: int | None = None
        for index, chunk in enumerate(chunks):
            first = index == 0
            outcome: dict[str, bool] = {}
            msg = await self._send_chunk(
                chat_id,
                chunk,
                reply_parameters if first else None,
                reply_markup if first else None,
                message_thread_id,
                outcome,
            )
            if msg is not None:
                delivered = delivered if delivered is not None else msg.message_id
                continue
            if outcome.get("transient") and self._outbox is not None:
                await self._enqueue_rest(
                    chat_id,
                    chunks,
                    index,
                    outbox_kind,
                    dedup_key,
                    message_thread_id,
                    reply_to_message_id if first else None,
                    reply_markup if first else None,
                )
            return delivered
        return delivered

    async def _enqueue_rest(
        self,
        chat_id: int,
        chunks: list[str],
        start: int,
        kind: str,
        dedup_key: str | None,
        thread: int | None,
        reply_to_message_id: int | None,
        reply_markup: Any,
    ) -> None:
        assert self._outbox is not None
        split = len(chunks) > 1 or start > 0
        for n in range(start, len(chunks)):
            payload: dict[str, Any] = {"text": chunks[n]}
            if n == start:
                if reply_to_message_id is not None:
                    payload["reply_to_message_id"] = reply_to_message_id
                if reply_markup is not None:
                    payload["reply_markup"] = reply_markup.model_dump(mode="json", exclude_none=True)
            key = f"{dedup_key}#{n}" if dedup_key is not None and split else dedup_key
            await self._outbox.enqueue(
                chat_id, kind, payload, now=datetime.now(UTC), dedup_key=key, message_thread_id=thread
            )

    async def edit_text(self, chat_id: int, message_id: int, text: str, *, reply_markup: Any = None) -> bool:
        msg = await send_with_retry(
            lambda: self.bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup
            ),
            what="правка текста",
        )
        return msg is not None

    async def edit_markup(self, chat_id: int, message_id: int, *, reply_markup: Any = None) -> bool:
        msg = await send_with_retry(
            lambda: self.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=message_id, reply_markup=reply_markup
            ),
            what="правка разметки",
        )
        return msg is not None

    async def edit_caption(
        self, chat_id: int, message_id: int, caption: str, *, reply_markup: Any = None, photo: bool = True
    ) -> bool:
        if not photo:
            return await self.edit_text(chat_id, message_id, caption, reply_markup=reply_markup)
        msg = await send_with_retry(
            lambda: self.bot.edit_message_caption(
                chat_id=chat_id, message_id=message_id, caption=caption, reply_markup=reply_markup
            ),
            what="правка подписи",
        )
        return msg is not None
