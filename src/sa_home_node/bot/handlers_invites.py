from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import CallbackQuery, Message

from sa_home_node.bot import commands, gateguests, guestsview
from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.context import GuestUsage, OnAdmitted
from sa_home_node.bot.gate import Admission, Gatekeeper, format_code
from sa_home_node.bot.guestfile import GuestStore
from sa_home_node.bot.menu import refresh_chat_menu
from sa_home_node.bot.notifier import Notifier
from sa_home_node.bot.rights import RightsCatalog
from sa_home_node.bot.servicelink import ServiceLink
from sa_home_node.bot.subs import Subscription
from sa_home_node.bot.ui import clamp_offset, int_or, part, redraw

router = Router(name="invites")

GROUP_TYPES = ("group", "supergroup")
WELCOME_TEXT = "🔑 Приглашение принято — добро пожаловать."
NO_INVITES_TEXT = (
    "⚠️ Приглашения выключены: боту некуда записать гостя (нет пакета инстанса). "
    "Запустите бота с <code>--instance</code>, тогда гостевые подписки будут "
    "жить рядом с основным пакетом и реплицироваться по рою."
)
MAX_INVITE_HOURS = 720.0
BAD_INVITE_HOURS_TEXT = (
    f"ошибка: часы должны быть числом больше 0 и не больше {MAX_INVITE_HOURS:g} "
    "(пример: <code>/invite 3</code>)"
)
GUEST_GONE_TEXT = "Такого гостя уже нет"


class JustAdmitted(Filter):
    async def __call__(self, message: Message, admission: Admission | None = None) -> bool:
        return admission is not None


class CodeFromInsider(Filter):
    async def __call__(self, message: Message, gate: Gatekeeper) -> bool | dict[str, Any]:
        if message.chat.type in GROUP_TYPES:
            return False
        known = await gate.known_code(message.text)
        if known is None:
            return False
        code, row = known
        return {"insider_code": code, "insider_code_row": row}


def _plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def _format_expiry(expires: datetime) -> str:
    left = expires - datetime.now(tz=UTC)
    total_minutes = max(-(-int(left.total_seconds()) // 60), 0)
    if total_minutes < 90:
        return f"{total_minutes} {_plural(total_minutes, 'минута', 'минуты', 'минут')}"
    total_hours = -(-total_minutes // 60)
    if total_hours < 48:
        return f"{total_hours} {_plural(total_hours, 'час', 'часа', 'часов')}"
    days = -(-total_hours // 24)
    return f"{days} {_plural(days, 'день', 'дня', 'дней')}"


def _parse_invite_hours(raw: str | None) -> float | None:
    if raw is None or not raw.strip():
        return None
    hours = float(raw.strip().replace(",", "."))
    if not (0 < hours <= MAX_INVITE_HOURS):
        raise ValueError(f"часы вне диапазона: {hours}")
    return hours


def _sorted_guests(book: SubscriptionBook) -> list[Subscription]:
    return sorted(book.guests(), key=lambda g: g.invited_at)


@router.message(Command("invite"))
async def cmd_invite(
    message: Message,
    command: CommandObject,
    gate: Gatekeeper,
    bot_username: str,
    about_name: str,
) -> None:
    if not gate.enabled:
        await message.answer(NO_INVITES_TEXT)
        return
    try:
        hours = _parse_invite_hours(command.args)
    except ValueError:
        await message.answer(BAD_INVITE_HOURS_TEXT)
        return
    sender = message.from_user
    code, expires = await gate.issue(
        message.chat.id,
        sender.id if sender else None,
        ttl_s=hours * 3600.0 if hours is not None else None,
    )
    link = f"https://t.me/{bot_username}?start={code}"
    await message.answer(
        "🔑 <b>Код приглашения</b>\n\n"
        f"<code>{format_code(code)}</code>\n\n"
        f"{link}\n\n"
        f"<i>Сообщите код {about_name} или воспользуйтесь ссылкой. "
        f"Инвайт доступен в течение {_format_expiry(expires)}.</i>"
    )


@router.message(Command("guests"))
async def cmd_guests(message: Message, book: SubscriptionBook) -> None:
    text, keyboard = guestsview.build_list_view(_sorted_guests(book), 0)
    await message.answer(text, reply_markup=keyboard)


async def _guest_or_redraw_list(
    callback: CallbackQuery, book: SubscriptionBook, chat_id: int
) -> Subscription | None:
    sub = book.for_chat(chat_id)
    if sub is not None and sub.is_guest:
        return sub
    await callback.answer(GUEST_GONE_TEXT, show_alert=True)
    text, keyboard = guestsview.build_list_view(_sorted_guests(book), 0)
    await redraw(callback, text, keyboard)
    return None


async def _redraw_perms(
    callback: CallbackQuery, sub: Subscription, offset: int, catalog: RightsCatalog
) -> None:
    offset = clamp_offset(offset, guestsview.PERM_PAGE_SIZE, guestsview.granted_rows(sub, catalog))
    text, keyboard = guestsview.build_perms_view(sub, offset, catalog)
    await redraw(callback, text, keyboard)


@router.callback_query(F.data.startswith(f"{commands.CALLBACK_PREFIX}:g_"))
async def on_guest_screen(
    callback: CallbackQuery,
    gate: Gatekeeper,
    book: SubscriptionBook,
    guest_store: GuestStore,
    registry: commands.Registry,
    catalog: RightsCatalog,
    guest_usage: GuestUsage,
    bot: Bot,
) -> None:
    parts = (callback.data or "").split(":")
    code = part(parts, 1) or ""
    chat_id = int_or(part(parts, 2), -1)
    offset = int_or(part(parts, 3), 0)
    list_offset = int_or(part(parts, 2), 0)

    if code == commands.GUESTS_LIST_CODE:
        text, keyboard = guestsview.build_list_view(_sorted_guests(book), list_offset)
        await redraw(callback, text, keyboard)
        await callback.answer()
        return

    if code == commands.OPEN_CODES_LIST_CODE:
        text, keyboard = guestsview.build_codes_view(await gate.open_codes(), list_offset)
        await redraw(callback, text, keyboard)
        await callback.answer()
        return

    if code == commands.CODE_REVOKE_CODE:
        ok = await gate.revoke_code(part(parts, 2) or "")
        toast = "Код отозван" if ok else "Код уже погашен или отозван"
        await callback.answer(toast, show_alert=not ok)
        text, keyboard = guestsview.build_codes_view(await gate.open_codes(), 0)
        await redraw(callback, text, keyboard)
        return

    if code == commands.GUEST_REVOKE_CODE:
        if gateguests.revoke_guest(book, guest_store, chat_id):
            await refresh_chat_menu(bot, chat_id, None, registry)
            await callback.answer("Гость выставлен")
            if callback.message:
                await callback.message.answer(f"🚪 Гость <code>{chat_id}</code> больше не впущен.")
        else:
            await callback.answer(GUEST_GONE_TEXT, show_alert=True)
        text, keyboard = guestsview.build_list_view(_sorted_guests(book), 0)
        await redraw(callback, text, keyboard)
        return

    if code == commands.GUEST_PERM_OFF_CODE:
        updated = gateguests.remove_guest_right(book, guest_store, chat_id, part(parts, 4) or "")
        if updated is None:
            await _guest_or_redraw_list(callback, book, chat_id)
            return
        await refresh_chat_menu(bot, chat_id, updated, registry)
        await callback.answer("Право снято")
        await _redraw_perms(callback, updated, offset, catalog)
        return

    if code == commands.GUEST_PERM_ADD_CODE:
        updated = gateguests.add_guest_right(book, guest_store, chat_id, part(parts, 4) or "")
        if updated is None:
            await _guest_or_redraw_list(callback, book, chat_id)
            return
        await refresh_chat_menu(bot, chat_id, updated, registry)
        await callback.answer("Право выдано")
        text, keyboard = guestsview.build_perms_view(updated, 0, catalog)
        await redraw(callback, text, keyboard)
        return

    if code not in _GUEST_CARD_CODES:
        await callback.answer()
        return

    sub = await _guest_or_redraw_list(callback, book, chat_id)
    if sub is None:
        return

    if code in (commands.GUEST_GROUP_ADD_CODE, commands.GUEST_GROUP_OFF_CODE):
        group = catalog.group(part(parts, 4) or "")
        if group is None:
            await callback.answer()
            return
        adding = code == commands.GUEST_GROUP_ADD_CODE
        rights = (
            catalog.apply_group_on(sub.allowed_commands, group)
            if adding
            else catalog.apply_group_off(sub.allowed_commands, group)
        )
        updated = gateguests.set_guest_rights(book, guest_store, chat_id, rights)
        if updated is None:
            await _guest_or_redraw_list(callback, book, chat_id)
            return
        await refresh_chat_menu(bot, chat_id, updated, registry)
        await callback.answer(f"{group.title}: {'выдано' if adding else 'снято'}")
        await _redraw_perms(callback, updated, 0 if adding else offset, catalog)
        return

    if code == commands.GUEST_STATS_CODE:
        body = await guest_usage(chat_id)
        text, keyboard = guestsview.build_back_to_card_view(sub.name, chat_id, body)
    elif code == commands.GUEST_CARD_CODE:
        text, keyboard = guestsview.build_card_view(sub)
    elif code == commands.GUEST_KICK_CONFIRM_CODE:
        text, keyboard = guestsview.build_kick_confirm_view(sub)
    elif code == commands.GUEST_PERMS_CODE:
        text, keyboard = guestsview.build_perms_view(sub, offset, catalog)
    else:
        text, keyboard = guestsview.build_perm_add_view(sub, offset, catalog)
    await redraw(callback, text, keyboard)
    await callback.answer()


_GUEST_CARD_CODES = frozenset(
    {
        commands.GUEST_CARD_CODE,
        commands.GUEST_KICK_CONFIRM_CODE,
        commands.GUEST_STATS_CODE,
        commands.GUEST_PERMS_CODE,
        commands.GUEST_PERM_ADD_LIST_CODE,
        commands.GUEST_GROUP_ADD_CODE,
        commands.GUEST_GROUP_OFF_CODE,
    }
)


@router.message(CodeFromInsider())
async def on_code_from_insider(
    message: Message,
    insider_code: str,
    insider_code_row: dict[str, Any],
    notifier: Notifier,
) -> None:
    if insider_code_row.get("redeemed_at"):
        await message.answer("🔑 Этот код уже использован — второй раз он не сработает.")
        return
    if insider_code_row.get("revoked_at"):
        await message.answer("🔑 Этот код отозван.")
        return
    await message.answer(
        "🔑 Это код приглашения — вам он не нужен, вы и так у меня в списке.\n\n"
        "Код нужен тому, кого приглашаете: пусть он сам пришлёт его мне в личку "
        "или откроет ссылку из приглашения."
    )
    issuer = insider_code_row.get("issued_by_chat_id")
    if issuer and issuer != message.chat.id:
        await notifier.send_direct(
            issuer,
            f"ℹ️ Код <code>{format_code(insider_code)}</code> прислали мне из чата "
            f"<code>{message.chat.id}</code>, который и так подписан — я объяснил, что он "
            "здесь не нужен. Если код ушёл не тому, отзовите его в /guests.",
        )


@router.message(JustAdmitted())
async def on_admitted(
    message: Message,
    admission: Admission,
    bot: Bot,
    apps_link: ServiceLink,
    registry: commands.Registry,
    notifier: Notifier,
    on_admitted: OnAdmitted | None = None,
) -> None:
    subscription = admission.subscription
    app_actions = await apps_link.actions()
    await refresh_chat_menu(bot, message.chat.id, subscription, registry, app_actions)

    if admission.invited_by_chat_id and admission.invited_by_chat_id != message.chat.id:
        await notifier.send_direct(
            admission.invited_by_chat_id,
            f"✅ Приглашение использовано: {escape(subscription.name)} (<code>{message.chat.id}</code>).",
        )

    if on_admitted is not None:
        await on_admitted(message, admission)
        return
    await message.answer(WELCOME_TEXT)
