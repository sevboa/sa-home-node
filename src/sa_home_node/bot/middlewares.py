from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, Update

from sa_home_node.bot import commands
from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.subs import Subscription

if TYPE_CHECKING:
    from sa_home_node.bot.gate import Gatekeeper

log = logging.getLogger(__name__)

DENIED_TEXT = "⛔️ Команда недоступна в этом чате."
DENIED_ALERT_TEXT = "⛔️ Недоступно"

Guard = Callable[[Subscription, str, str], bool]


def _update_chat_id(update: Update) -> int | None:
    for event in (
        update.message,
        update.edited_message,
        update.channel_post,
        update.edited_channel_post,
        update.my_chat_member,
        update.chat_member,
    ):
        if event is not None and event.chat is not None:
            return event.chat.id
    callback = update.callback_query
    if callback is not None and callback.message is not None and callback.message.chat:
        return callback.message.chat.id
    return None


def _user_name(message: Message | None) -> str | None:
    user = message.from_user if message else None
    if user is None:
        return None
    return f"{user.full_name} (@{user.username})" if user.username else user.full_name


def invite_candidate(message: Message | None) -> str | None:
    if message is None or not message.text:
        return None
    text = message.text.strip()
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else None
    return text


def extract_command(text: str | None) -> str | None:
    if not text or not text.startswith("/"):
        return None
    head = text.split(maxsplit=1)[0]
    return head[1:].split("@", 1)[0]


class SilenceGate(BaseMiddleware):
    def __init__(self, book: SubscriptionBook, gate: Gatekeeper) -> None:
        self._book = book
        self._gate = gate

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        chat_id = _update_chat_id(event)
        if chat_id is None:
            return None
        if self._book.for_chat(chat_id) is not None:
            return await handler(event, data)

        message = event.message
        admission = await self._gate.try_admit(
            chat_id,
            invite_candidate(message),
            user_id=message.from_user.id if message and message.from_user else None,
            user_name=_user_name(message),
            chat_title=message.chat.title if message and message.chat else None,
        )
        if admission is None:
            log.debug("Гейт: апдейт из неподписного chat_id=%s проигнорирован", chat_id)
            return None
        data["admission"] = admission
        return await handler(event, data)


class AuthorizationMiddleware(BaseMiddleware):
    def __init__(self, book: SubscriptionBook, registry: commands.Registry) -> None:
        self._book = book
        self._registry = registry

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        chat_id = event.chat.id if event.chat else None
        subscription = self._book.for_chat(chat_id) if chat_id is not None else None
        data["subscription"] = subscription

        command = extract_command(event.text)
        if command is None or self._registry.is_universal(command):
            return await handler(event, data)

        if self._registry.is_control(command):
            right = self._registry.required_right(command)
            if subscription is None or not subscription.allows_command(right):
                log.info("Отказ в /%s для chat_id=%s", command, chat_id)
                await event.answer(DENIED_TEXT)
                return None

        return await handler(event, data)


class CallbackAuthorizationMiddleware(BaseMiddleware):
    def __init__(
        self,
        book: SubscriptionBook,
        registry: commands.Registry,
        guards: tuple[Guard, ...] = (),
    ) -> None:
        self._book = book
        self._registry = registry
        self._guards = guards

    async def __call__(
        self,
        handler: Callable[[CallbackQuery, dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        chat = event.message.chat if event.message else None
        chat_id = chat.id if chat else None
        subscription = self._book.for_chat(chat_id) if chat_id is not None else None
        data["subscription"] = subscription

        action = commands.parse_action_callback(event.data)
        if action is not None:
            service, action_id, _, _ = action
            allowed = subscription is not None and (
                subscription.allows_action(action_id, service)
                or any(guard(subscription, service, action_id) for guard in self._guards)
            )
            if not allowed:
                log.info("Отказ в действии %s для chat_id=%s", event.data, chat_id)
                await event.answer(DENIED_ALERT_TEXT, show_alert=True)
                return None
            return await handler(event, data)

        cmd = self._registry.command_for_callback(event.data)
        if cmd is None:
            return await handler(event, data)

        if subscription is None or not subscription.allows_command(cmd.right or cmd.name):
            log.info("Отказ в кнопке %s для chat_id=%s", event.data, chat_id)
            await event.answer(DENIED_ALERT_TEXT, show_alert=True)
            return None

        return await handler(event, data)
