from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputRichMessage,
)

BACK_TEXT = "⬅️ Назад"
REFRESH_TEXT = "🔄 Обновить"


def pack(prefix: str, *parts: Any) -> str:
    items = [prefix] + [str(p) for p in parts]
    return ":".join(items)


def unpack(data: str | None, prefix: str, parts: int) -> list[str] | None:
    if data is None:
        return None
    if not data.startswith(prefix + ":"):
        if parts == 0 and data == prefix:
            return []
        return None
    rest = data[len(prefix) + 1 :]
    split = rest.split(":")
    if len(split) != parts:
        return None
    return split


def unpack_id_and_button(data: str | None, prefix: str) -> list[Any] | None:
    if data is None:
        return None
    if not data.startswith(prefix + ":"):
        return None
    rest = data[len(prefix) + 1 :]
    split = rest.split(":", 1)
    if len(split) != 2:
        return None
    id_str, button = split
    if not id_str.isdigit():
        return None
    return [int(id_str), button]


def int_or(raw: str | None, default: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def part(parts: list[str], index: int) -> str | None:
    if 0 <= index < len(parts):
        return parts[index]
    return None


def button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def markup(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    inline_keyboard = [list(row) for row in rows]
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)


def single_button(text: str, data: str) -> InlineKeyboardMarkup:
    return markup([button(text, data)])


def back_row(data: str, text: str = BACK_TEXT) -> list[InlineKeyboardButton]:
    return [button(text, data)]


def refresh_row(data: str) -> list[InlineKeyboardButton]:
    return [button(REFRESH_TEXT, data)]


def clamp_offset(offset: int, size: int, total: int) -> int:
    if total == 0:
        return 0
    max_offset = ((total - 1) // size) * size
    if offset < 0:
        return 0
    if offset > max_offset:
        return max_offset
    return offset


def nav_row(offset: int, size: int, total: int, callback: Callable[[int], str]) -> list[InlineKeyboardButton]:
    buttons: list[InlineKeyboardButton] = []
    if offset > 0:
        buttons.append(button("⬅️", callback(max(0, offset - size))))
    if offset + size < total:
        buttons.append(button("➡️", callback(offset + size)))
    return buttons


async def redraw(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None) -> None:
    message = callback.message
    if message is None:
        return
    try:
        await message.edit_text(text=text, reply_markup=markup)
    except TelegramBadRequest:
        pass


async def redraw_rich(
    callback: CallbackQuery,
    rich: InputRichMessage,
    markup: InlineKeyboardMarkup | None,
) -> None:
    message = callback.message
    if message is None:
        return
    try:
        await message.edit_text(rich_message=rich, reply_markup=markup)
    except TelegramBadRequest:
        pass


def format_duration(seconds: float) -> str:
    total = max(int(seconds), 0)
    parts: list[str] = []
    for name, value in (("д", 86400), ("ч", 3600), ("м", 60)):
        unit = total // value
        if unit:
            parts.append(f"{unit}{name}")
        total %= value
    if total or not parts:
        parts.append(f"{total}с")
    return " ".join(parts)
