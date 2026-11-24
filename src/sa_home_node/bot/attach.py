from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, InputMediaPhoto, ReplyParameters

from sa_home_node.bot.send import send_with_retry

log = logging.getLogger(__name__)

MAX_GROUP = 10


def _file(value: bytes | str, default: str, name: str | None) -> BufferedInputFile | str:
    return value if isinstance(value, str) else BufferedInputFile(value, filename=name or default)


class AttachMixin:
    bot: Bot

    async def send_document(
        self,
        chat_id: int,
        document: bytes | str,
        filename: str | None = None,
        caption: str | None = None,
        message_thread_id: int | None = None,
    ) -> tuple[int, str] | None:
        file = _file(document, "file", filename)
        msg = await send_with_retry(
            lambda: self.bot.send_document(
                chat_id, file, caption=caption, message_thread_id=message_thread_id
            ),
            what="документ",
        )
        return None if msg is None else (msg.message_id, msg.document.file_id)

    async def send_photo(
        self,
        chat_id: int,
        photo: bytes | str,
        filename: str | None = None,
        caption: str | None = None,
        message_thread_id: int | None = None,
    ) -> int | None:
        file = _file(photo, "photo.png", filename)
        msg = await send_with_retry(
            lambda: self.bot.send_photo(chat_id, file, caption=caption, message_thread_id=message_thread_id),
            what="фото",
        )
        return None if msg is None else msg.message_id

    async def send_photo_ex(
        self,
        chat_id: int,
        photo: bytes | str,
        filename: str | None = None,
        caption: str | None = None,
        message_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
        has_spoiler: bool = False,
        reply_markup: Any = None,
    ) -> tuple[int, str] | None:
        file = _file(photo, "image.png", filename)
        reply_parameters = (
            None
            if reply_to_message_id is None
            else ReplyParameters(message_id=reply_to_message_id, allow_sending_without_reply=True)
        )
        extra: dict[str, Any] = {} if reply_markup is None else {"reply_markup": reply_markup}
        msg = await send_with_retry(
            lambda: self.bot.send_photo(
                chat_id,
                file,
                caption=caption,
                message_thread_id=message_thread_id,
                reply_parameters=reply_parameters,
                has_spoiler=has_spoiler,
                **extra,
            ),
            what="фото",
        )
        return None if msg is None else (msg.message_id, msg.photo[-1].file_id)

    async def send_media_group(
        self,
        chat_id: int,
        photos: list[bytes],
        caption: str | None = None,
        message_thread_id: int | None = None,
    ) -> list[int] | None:
        photos = photos[:MAX_GROUP]
        if not photos:
            return None
        if len(photos) == 1:
            msg = await self.send_photo(
                chat_id, photos[0], caption=caption, message_thread_id=message_thread_id
            )
            return None if msg is None else [msg]
        media = [
            InputMediaPhoto(
                media=BufferedInputFile(data, filename=f"photo{index}.png"),
                caption=caption if index == 1 else None,
                parse_mode="HTML",
            )
            for index, data in enumerate(photos, 1)
        ]
        msgs = await send_with_retry(
            lambda: self.bot.send_media_group(chat_id, media, message_thread_id=message_thread_id),
            what="альбом",
        )
        return None if msgs is None else [msg.message_id for msg in msgs]

    async def send_voice(
        self,
        chat_id: int,
        voice: bytes | str,
        filename: str | None = None,
        caption: str | None = None,
        message_thread_id: int | None = None,
    ) -> int | None:
        file = _file(voice, "voice.ogg", filename)
        msg = await send_with_retry(
            lambda: self.bot.send_voice(chat_id, file, caption=caption, message_thread_id=message_thread_id),
            what="голос",
        )
        return None if msg is None else msg.message_id

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        try:
            await self.bot.delete_message(chat_id, message_id)
        except TelegramAPIError as exc:
            log.warning("Не удалось удалить сообщение в чате %s: %s", chat_id, exc)
