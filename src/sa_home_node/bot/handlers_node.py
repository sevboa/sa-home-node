from __future__ import annotations

import contextlib
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from sa_home_node.bot import actions, commands, nodeview
from sa_home_node.bot.db import AppState
from sa_home_node.bot.servicelink import ServiceLink
from sa_home_node.bot.statusview import SERVICE_CALL_ERRORS
from sa_home_node.bot.subs import Subscription
from sa_home_node.bot.ui import redraw
from sa_home_node.proto.wire import (
    NODE_SERVICE,
    ProtoError,
    ServiceUnavailableError,
    node_address,
)
from sa_home_node.wake.peers import MONITOR_SERVICE

log = logging.getLogger(__name__)

router = Router(name="node")

SELF_SERVICE_NAME = "telegram-bot"
ACTION_RESTART_NODE = "restart_node"
ACTION_CHECK_UPDATE = "check_update"
ACTION_UNASSIGN = "unassign"

SELF_RESTART_TEXT = "🔄 Перезапускаюсь, вернусь через минуту."
SELF_STOP_TEXT = (
    "⏹ Останавливаюсь. Запустить меня снова можно только с ноды: <code>nodectl start telegram-bot</code>."
)

SELF_RESTART_CB_KEY = "self_restart_cb"


def _is_self_shutdown(action_id: str, value: str | None, node_id: str | None) -> bool:
    if node_id is not None:
        return False
    if action_id == ACTION_RESTART_NODE:
        return True
    return action_id in ("stop", "restart") and value == SELF_SERVICE_NAME


async def _handle_self_shutdown(
    callback: CallbackQuery,
    message: Message,
    state: AppState,
    node_link: ServiceLink,
    action_id: str,
    value: str | None,
) -> None:
    if await state.get(SELF_RESTART_CB_KEY) == callback.id:
        log.warning("Повторный callback само-рестарта %s — игнорирую", callback.id)
        await callback.answer()
        return
    await state.set(SELF_RESTART_CB_KEY, callback.id)

    await callback.answer()
    await message.answer(SELF_STOP_TEXT if action_id == "stop" else SELF_RESTART_TEXT)
    result: dict = {}
    with contextlib.suppress(*SERVICE_CALL_ERRORS, TimeoutError):
        args = {"name": value} if value else {}
        result = await node_link.command(action_id, args)
    if action_id == ACTION_RESTART_NODE and result.get("deferred") is not None:
        await message.answer(nodeview.restart_deferred_text(result))


async def _run_node_action(
    node_link: ServiceLink, action_id: str, value: str | None, node_id: str | None
) -> tuple[str | None, dict | None]:
    node_actions = await nodeview.fetch_node_actions(node_link, node_id)
    action = next((a for a in node_actions if a.id == action_id), None)
    if action is None:
        return "Действие недоступно.", None
    try:
        result = await node_link.command(
            action.id, actions.build_args(action, value), dst=node_address(node_id)
        )
    except ServiceUnavailableError:
        return nodeview.node_unavailable_text(node_id), None
    except ProtoError as exc:
        return f"⚠️ Ошибка: {exc.message}", None
    return None, result


def _format_check_update(result: dict) -> str:
    running = result.get("running", "?")
    installed = result.get("installed") or "?"
    latest = str(result.get("latest", "?")).lstrip("v")
    if latest not in ("?", installed):
        return (
            f"⬆️ Доступно обновление: v{latest} (сейчас установлено v{installed}, "
            f"запущено v{running}) — нажмите «Обновить»"
        )
    return f"✅ Обновлений нет — установлена последняя версия (v{installed})"


async def _node_action_screen(
    node_link: ServiceLink,
    subscription: Subscription | None,
    action_id: str,
    value: str | None,
    node_id: str | None,
) -> tuple[str, InlineKeyboardMarkup | None]:
    if action_id in (nodeview.ACTION_ASSIGN, ACTION_UNASSIGN):
        if subscription is not None and subscription.allows_command(nodeview.NODES_RIGHT):
            return await nodeview.build_services_page_view(node_link, subscription, node_id, 0)
        return await nodeview.build_node_card_view(node_link, subscription, node_id)
    if value is not None:
        return await nodeview.build_service_card_view(node_link, subscription, value, node_id)
    return await nodeview.build_node_card_view(node_link, subscription, node_id)


async def _handle_node_action(
    callback: CallbackQuery,
    message: Message,
    node_link: ServiceLink,
    subscription: Subscription | None,
    action_id: str,
    value: str | None,
    node_id: str | None,
) -> None:
    error, result = await _run_node_action(node_link, action_id, value, node_id)
    if error is not None:
        await message.answer(error)
    elif action_id == ACTION_CHECK_UPDATE:
        await message.answer(_format_check_update(result or {}))
    else:
        text, keyboard = await _node_action_screen(node_link, subscription, action_id, value, node_id)
        await redraw(callback, text, keyboard)
    await callback.answer("Готово" if error is None else None)


async def _handle_apps_action(
    callback: CallbackQuery,
    message: Message,
    apps_link: ServiceLink,
    subscription: Subscription | None,
    action_id: str,
    value: str | None,
) -> None:
    text, keyboard = await actions.run_app_skill(apps_link, subscription, action_id, value)
    with contextlib.suppress(TelegramBadRequest):
        await message.edit_text(text=text, reply_markup=keyboard, disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(F.data.startswith(f"{commands.ACTION_CALLBACK_PREFIX}:"))
async def on_dynamic_action(
    callback: CallbackQuery,
    state: AppState,
    node_link: ServiceLink,
    apps_link: ServiceLink,
    subscription: Subscription | None = None,
) -> None:
    parsed = commands.parse_action_callback(callback.data)
    message = callback.message
    if parsed is None or not isinstance(message, Message):
        await callback.answer()
        return
    service, action_id, value, node_id = parsed

    if service == NODE_SERVICE and _is_self_shutdown(action_id, value, node_id):
        await _handle_self_shutdown(callback, message, state, node_link, action_id, value)
    elif service == NODE_SERVICE:
        await _handle_node_action(callback, message, node_link, subscription, action_id, value, node_id)
    elif service == actions.APPS_SERVICE:
        await _handle_apps_action(callback, message, apps_link, subscription, action_id, value)
    elif service == MONITOR_SERVICE:
        text = await actions.run_action(node_link, service, action_id, value, node_id=node_id)
        await message.answer(text)
        await callback.answer()
    else:
        log.warning("Callback для неизвестной службы: %s", callback.data)
        await callback.answer("Неизвестная служба", show_alert=True)
