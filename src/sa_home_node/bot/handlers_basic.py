from __future__ import annotations

import logging
from collections.abc import Sequence

from aiogram import F, Router, types
from aiogram.filters import Command

from sa_home_node import __version__
from sa_home_node.bot import commands
from sa_home_node.bot.actions import APPS_SERVICE, run_app_skill, visible_skill_actions
from sa_home_node.bot.config import BotConfig
from sa_home_node.bot.middlewares import DENIED_TEXT
from sa_home_node.bot.servicelink import ServiceLink
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.describe import ActionSpec

log = logging.getLogger(__name__)

router = Router(name="basic")


@router.message(Command("start", ignore_mention=True))
async def cmd_start(
    message: types.Message,
    apps_link: ServiceLink,
    registry: commands.Registry,
    bot_config: BotConfig,
    help_lines: tuple[str, ...],
    subscription: Subscription | None,
) -> None:
    skills = await apps_link.actions()
    help_text = build_help(subscription, skills, registry, bot_config.about_name, help_lines)
    greeting = "👋 Я бот роя домашних машин: скилы роя — прямо в меню команд."
    await message.answer(f"{greeting}\n\n{help_text}")


@router.message(Command("ping", ignore_mention=True))
async def cmd_ping(message: types.Message) -> None:
    await message.answer("🏓 pong")


@router.message(F.text.startswith("/"))
async def cmd_skill(
    message: types.Message,
    apps_link: ServiceLink,
    subscription: Subscription | None,
) -> None:
    text = message.text
    if text is None:
        return
    action_id = text.split(maxsplit=1)[0][1:].split("@", 1)[0]

    actions = await apps_link.actions()
    action = next((a for a in actions if a.id == action_id), None)
    if action is None:
        return

    if subscription is None or not subscription.allows_action(action_id, APPS_SERVICE):
        await message.answer(DENIED_TEXT)
        return

    result_text, markup = await run_app_skill(apps_link, subscription, action_id)
    await message.answer(result_text, reply_markup=markup, disable_web_page_preview=True)


def build_help(
    subscription: Subscription | None,
    skill_actions: Sequence[ActionSpec],
    registry: commands.Registry,
    about_name: str,
    help_lines: tuple[str, ...] = (),
) -> str:
    lines: list[str] = ["<b>Доступные команды</b>", ""]

    visible_skills = visible_skill_actions(subscription, skill_actions)
    for action in visible_skills:
        lines.append(f"/{action.id} — {action.title}: состояние и веб-морда")

    control_cmds = registry.menu_control_commands_for(subscription)
    for cmd in control_cmds:
        lines.append(f"/{cmd.name} — {cmd.description}")

    if visible_skills or control_cmds:
        lines.append("")

    universal_cmds = registry.menu_universal_commands()
    for cmd in universal_cmds:
        lines.append(f"/{cmd.name} — {cmd.description}")

    lines.append("")

    if help_lines:
        lines.extend(help_lines)
        lines.append("")

    lines.append(f"ℹ️ {about_name} v{__version__}")
    return "\n".join(lines)
