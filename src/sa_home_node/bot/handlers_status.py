from __future__ import annotations

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from sa_home_node.bot import actions, commands, nodeview, scanlimit, statusview, swarmview
from sa_home_node.bot.commands import Registry
from sa_home_node.bot.db import AppState
from sa_home_node.bot.servicelink import ServiceLink
from sa_home_node.bot.subs import Subscription
from sa_home_node.bot.ui import int_or, part
from sa_home_node.proto.wire import Address, ProtoError, ServiceUnavailableError
from sa_home_node.wake.peers import MONITOR_SERVICE

router = Router(name="status")

SCAN_ACTION = "scan_now"
UNKNOWN_ACTION_TEXT = "Неизвестное действие."


def _node_arg(parts: list[str], index: int) -> str | None:
    return part(parts, index) or None


async def _scan_now(link: ServiceLink, state: AppState) -> str:
    dst = Address(node=None, service=MONITOR_SERVICE)
    action = await actions.find_action(link, MONITOR_SERVICE, SCAN_ACTION)
    if action is None:
        return actions.unavailable_text(link) if not link.connected else "Действие недоступно."

    key = f"{MONITOR_SERVICE}:{SCAN_ACTION}"
    decision = scanlimit.decide(await scanlimit.get_action_ticks(state, key), datetime.now(tz=UTC))
    if not decision.allowed:
        return decision.reason

    try:
        result = await link.command(action.id, actions.build_args(action, None), dst=dst)
    except ServiceUnavailableError:
        return actions.unavailable_text(link)
    except ProtoError as exc:
        return f"⚠️ Ошибка: {exc.message}"
    except (ConnectionError, OSError, TimeoutError):
        return f"⚠️ Служба «{link.display_name}» не ответила вовремя — попробуйте позже."

    text = actions.render_result(action, result)
    if text != actions.ALREADY_QUEUED_TEXT:
        await scanlimit.set_action_ticks(state, key, list(decision.ticks))
    return text


@router.message(Command("status"))
async def cmd_status(
    message: Message, node_link: ServiceLink, subscription: Subscription | None = None
) -> None:
    text, keyboard = await nodeview.build_node_card_view(node_link, subscription)
    await message.answer(text, reply_markup=keyboard)


@router.message(Command("status_full"))
async def cmd_status_full(message: Message, node_link: ServiceLink) -> None:
    await message.answer(await statusview.build_full_text(node_link, dst=statusview.monitor_dst(None)))


@router.message(Command("stats"))
async def cmd_stats(message: Message, node_link: ServiceLink) -> None:
    await message.answer(await statusview.build_stats_text(node_link, dst=statusview.monitor_dst(None)))


@router.message(Command("downtime"))
async def cmd_downtime(message: Message, node_link: ServiceLink) -> None:
    text, keyboard = await statusview.build_downtime_page(node_link)
    await message.answer(text, reply_markup=keyboard)


@router.message(Command("scan_now"))
async def cmd_scan_now(message: Message, node_link: ServiceLink, app_state: AppState) -> None:
    await message.answer(await _scan_now(node_link, app_state))


@router.callback_query(F.data.startswith(f"{commands.CALLBACK_PREFIX}:"))
async def on_status_action(
    callback: CallbackQuery,
    node_link: ServiceLink,
    registry: Registry,
    wake: object | None = None,
    app_state: AppState | None = None,
    subscription: Subscription | None = None,
) -> None:
    cmd = registry.command_for_callback(callback.data)
    if cmd is None or callback.message is None or callback.data is None:
        await callback.answer()
        return
    parts = callback.data.split(":")
    code = parts[1]

    if code == commands.NODES_CODE:
        text, keyboard = await swarmview.build_swarm_view(node_link, subscription, wake, app_state)
        await callback.message.answer(text, reply_markup=keyboard)
    elif code == commands.NODE_CARD_CODE:
        text, keyboard = await nodeview.build_node_card_view(node_link, subscription, _node_arg(parts, 2))
        await callback.message.answer(text, reply_markup=keyboard)
    elif code == commands.SERVICE_CARD_CODE:
        text, keyboard = await nodeview.build_service_card_view(
            node_link, subscription, part(parts, 2) or "", _node_arg(parts, 3)
        )
        await callback.message.answer(text, reply_markup=keyboard)
    elif code == "full":
        dst = statusview.monitor_dst(_node_arg(parts, 2))
        await callback.message.answer(await statusview.build_full_text(node_link, dst=dst))
    elif code == "stats":
        dst = statusview.monitor_dst(_node_arg(parts, 2))
        await callback.message.answer(await statusview.build_stats_text(node_link, dst=dst))
    elif code == "downtime":
        text, keyboard = await statusview.build_downtime_page(node_link, node_id=_node_arg(parts, 2))
        await callback.message.answer(text, reply_markup=keyboard)
    elif code == commands.DOWNTIME_PAGE_CODE:
        offset = max(0, int_or(part(parts, 2), 0))
        text, keyboard = await statusview.build_downtime_page(node_link, offset, node_id=_node_arg(parts, 3))
        await callback.message.edit_text(text, reply_markup=keyboard)
    else:
        await callback.message.answer(UNKNOWN_ACTION_TEXT)
    await callback.answer()
