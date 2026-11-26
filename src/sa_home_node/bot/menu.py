from __future__ import annotations

import logging
from collections.abc import Iterable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.commands import Registry
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.describe import ActionSpec

log = logging.getLogger(__name__)

SKILL_SERVICE = "apps"
SKILL_SUFFIX = ": состояние и веб-морда"


def build_menu_commands(
    subscription: Subscription | None,
    registry: Registry,
    skill_actions: Iterable[ActionSpec] = (),
) -> list[BotCommand]:
    commands: list[BotCommand] = []
    if subscription is not None:
        for action in skill_actions:
            if action.params or not registry.can_run_action(subscription, action.id, SKILL_SERVICE):
                continue
            commands.append(BotCommand(command=action.id, description=f"{action.title}{SKILL_SUFFIX}"))
        commands.extend(
            BotCommand(command=cmd.name, description=cmd.description)
            for cmd in registry.menu_control_commands_for(subscription)
        )
    commands.extend(
        BotCommand(command=cmd.name, description=cmd.description)
        for cmd in registry.menu_universal_commands()
    )
    return commands


async def refresh_chat_menu(
    bot: Bot,
    chat_id: int,
    subscription: Subscription | None,
    registry: Registry,
    skill_actions: Iterable[ActionSpec] = (),
) -> None:
    commands = build_menu_commands(subscription, registry, skill_actions=skill_actions)
    scope = BotCommandScopeChat(chat_id=chat_id).model_dump(mode="json")
    try:
        if subscription is None:
            await bot.delete_my_commands(scope=scope)
        else:
            await bot.set_my_commands(commands, scope=scope)
    except TelegramAPIError as exc:
        log.warning("Не удалось обновить меню чата chat_id=%s: %s", chat_id, exc)


async def set_bot_commands(
    bot: Bot,
    book: SubscriptionBook,
    registry: Registry,
    skill_actions: Iterable[ActionSpec] = (),
) -> None:
    try:
        await bot.delete_my_commands(scope=BotCommandScopeDefault().model_dump(mode="json"))
    except TelegramAPIError as exc:
        log.warning("Не удалось удалить глобальные команды бота: %s", exc)
    for sub in book.all():
        if sub.broken:
            continue
        await refresh_chat_menu(bot, sub.chat_id, sub, registry, skill_actions=skill_actions)
