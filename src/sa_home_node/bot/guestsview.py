from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from sa_home_node.bot import commands
from sa_home_node.bot.gate import format_code
from sa_home_node.bot.rights import RightsCatalog
from sa_home_node.bot.subs import Subscription
from sa_home_node.bot.ui import back_row, button, markup, nav_row, pack

GUEST_PAGE_SIZE = 10
CODE_PAGE_SIZE = 5
PERM_PAGE_SIZE = 6

_MAX_NAME_LEN = 40

View = tuple[str, InlineKeyboardMarkup]


def format_local_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso


def _with_nav(
    buttons: list[list[InlineKeyboardButton]],
    offset: int,
    size: int,
    total: int,
    code: str,
    *args: int,
) -> None:
    def target(o: int) -> str:
        return commands.panel_callback(code, *args, o)

    nav = nav_row(offset, size, total, target)
    if nav:
        buttons.append(nav)


def build_list_view(guests: Sequence[Subscription], offset: int) -> View:
    lines = [f"<b>Приглашённые</b> ({len(guests)})", ""]
    if not guests:
        lines.append("Пока никого.")
    page = guests[offset : offset + GUEST_PAGE_SIZE]
    for guest in page:
        lines.append(
            f"• {escape(guest.name)} — <code>{guest.chat_id}</code>"
            + (f", вход {format_local_time(guest.invited_at)}" if guest.invited_at else "")
        )
    buttons = [
        [
            button(
                f"👤 {guest.name}"[:_MAX_NAME_LEN],
                commands.panel_callback(commands.GUEST_CARD_CODE, guest.chat_id),
            )
        ]
        for guest in page
    ]
    _with_nav(buttons, offset, GUEST_PAGE_SIZE, len(guests), commands.GUESTS_LIST_CODE)
    buttons.append([button("🔑 Открытые коды", commands.panel_callback(commands.OPEN_CODES_LIST_CODE, 0))])
    return "\n".join(lines), markup(*buttons)


def build_codes_view(open_codes: Sequence[Mapping[str, str]], offset: int) -> View:
    lines = [f"<b>Открытые коды</b> ({len(open_codes)})", ""]
    if not open_codes:
        lines.append("Открытых кодов нет.")
    page = open_codes[offset : offset + CODE_PAGE_SIZE]
    for row in page:
        lines.append(f"• <code>{format_code(row['code'])}</code> — до {format_local_time(row['expires_at'])}")
    buttons = [
        [
            button(
                f"🔒 Отозвать {format_code(row['code'])}",
                commands.panel_callback(commands.CODE_REVOKE_CODE, row["code"]),
            )
        ]
        for row in page
    ]
    _with_nav(buttons, offset, CODE_PAGE_SIZE, len(open_codes), commands.OPEN_CODES_LIST_CODE)
    buttons.append(back_row(commands.panel_callback(commands.GUESTS_LIST_CODE, 0), "⬅️ К списку гостей"))
    return "\n".join(lines), markup(*buttons)


def build_card_view(sub: Subscription) -> View:
    lines = [f"👤 <b>{escape(sub.name)}</b>", "", f"chat_id: <code>{sub.chat_id}</code>"]
    if sub.invited_at:
        lines.append(f"Вход: {format_local_time(sub.invited_at)}")
    if sub.invited_user:
        lines.append(f"Представился: {escape(sub.invited_user)}")
    lines.append(f"Прав выдано: {len(sub.allowed_commands)}")
    return "\n".join(lines), markup(
        [
            button(
                f"🔐 Права ({len(sub.allowed_commands)})",
                commands.panel_callback(commands.GUEST_PERMS_CODE, sub.chat_id, 0),
            )
        ],
        [button("📊 Статистика VPN", commands.panel_callback(commands.GUEST_STATS_CODE, sub.chat_id))],
        [
            button(
                "🚪 Выставить",
                commands.panel_callback(commands.GUEST_KICK_CONFIRM_CODE, sub.chat_id),
            )
        ],
        back_row(commands.panel_callback(commands.GUESTS_LIST_CODE, 0)),
    )


def build_kick_confirm_view(sub: Subscription) -> View:
    text = (
        f"⚠️ Точно выставить <b>{escape(sub.name)}</b> (<code>{sub.chat_id}</code>)?\n"
        "Доступ пропадёт немедленно."
    )
    return text, markup(
        [button("✅ Да, выставить", commands.panel_callback(commands.GUEST_REVOKE_CODE, sub.chat_id))],
        back_row(commands.panel_callback(commands.GUEST_CARD_CODE, sub.chat_id), "⬅️ Отмена"),
    )


def build_back_to_card_view(sub_name: str, chat_id: int, body: str) -> View:
    text = f"📊 <b>{escape(sub_name)}</b>\n\n{body}"
    return text, markup(back_row(commands.panel_callback(commands.GUEST_CARD_CODE, chat_id)))


def _granted_entries(sub: Subscription, catalog: RightsCatalog) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    grouped: set[str] = set()
    for group in catalog.groups:
        if group.fine:
            continue
        rights = group.rights()
        granted = rights & sub.allowed_commands
        if not granted:
            continue
        grouped |= granted
        total = len(rights)
        suffix = "" if len(granted) == total else f" ({len(granted)}/{total})"
        entries.append((f"{group.title}{suffix}", commands.GUEST_GROUP_OFF_CODE, group.key))
    for right in sorted(sub.allowed_commands - grouped):
        entries.append((catalog.label(right), commands.GUEST_PERM_OFF_CODE, right))
    return entries


def granted_rows(sub: Subscription, catalog: RightsCatalog) -> int:
    return len(_granted_entries(sub, catalog))


def build_perms_view(sub: Subscription, offset: int, catalog: RightsCatalog) -> View:
    entries = _granted_entries(sub, catalog)
    lines = [f"🔐 <b>Права гостя {escape(sub.name)}</b> ({len(sub.allowed_commands)})", ""]
    if not entries:
        lines.append("Прав нет.")
    page = entries[offset : offset + PERM_PAGE_SIZE]
    for text, _code, _arg in page:
        lines.append(f"• {escape(text)}")
    buttons = [
        [
            button(
                f"➖ {text}"[:_MAX_NAME_LEN],
                pack(commands.CALLBACK_PREFIX, code, sub.chat_id, offset, arg),
            )
        ]
        for text, code, arg in page
    ]
    _with_nav(buttons, offset, PERM_PAGE_SIZE, len(entries), commands.GUEST_PERMS_CODE, sub.chat_id)
    buttons.append(
        [
            button(
                "➕ Добавить право",
                commands.panel_callback(commands.GUEST_PERM_ADD_LIST_CODE, sub.chat_id, 0),
            )
        ]
    )
    buttons.append(back_row(commands.panel_callback(commands.GUEST_CARD_CODE, sub.chat_id)))
    return "\n".join(lines), markup(*buttons)


def _addable_entries(sub: Subscription, catalog: RightsCatalog) -> list[tuple[str, str, str]]:
    return [
        (group.title, commands.GUEST_GROUP_ADD_CODE, group.key)
        for group in catalog.groups
        if not group.fine and not group.rights() <= sub.allowed_commands
    ]


def build_perm_add_view(sub: Subscription, offset: int, catalog: RightsCatalog) -> View:
    addable = _addable_entries(sub, catalog)
    lines = [f"➕ <b>Добавить право — {escape(sub.name)}</b>", ""]
    if not addable:
        lines.append("Все известные права уже выданы.")
    else:
        lines.append("Право выдаётся сразу, без перезапуска.")
    page = addable[offset : offset + PERM_PAGE_SIZE]
    buttons = [
        [
            button(
                f"➕ {text}"[:_MAX_NAME_LEN],
                pack(commands.CALLBACK_PREFIX, code, sub.chat_id, offset, arg),
            )
        ]
        for text, code, arg in page
    ]
    _with_nav(buttons, offset, PERM_PAGE_SIZE, len(addable), commands.GUEST_PERM_ADD_LIST_CODE, sub.chat_id)
    buttons.append(back_row(commands.panel_callback(commands.GUEST_PERMS_CODE, sub.chat_id, 0)))
    return "\n".join(lines), markup(*buttons)
