from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from sa_home_node.bot import commands, nodeview, swarmview
from sa_home_node.bot.actions import APPS_SERVICE, run_app_skill
from sa_home_node.bot.config import WakeConfig
from sa_home_node.bot.db import AppState
from sa_home_node.bot.servicelink import ServiceLink
from sa_home_node.bot.subs import Subscription
from sa_home_node.bot.ui import back_row, button, int_or, nav_row, part, redraw, refresh_row
from sa_home_node.proto.describe import ActionSpec
from sa_home_node.proto.wire import ProtoError, ServiceUnavailableError
from sa_home_node.services.periodic import PEER_ERRORS
from sa_home_node.wake.peers import NodeReport, collect_reports, fetch_state

log = logging.getLogger(__name__)

router = Router(name="swarm_panel")

NODES_PAGE_SIZE = 5
SKILLS_PAGE_SIZE = 6
CHECK_UPDATE_ACTION = "check_update"

ScreenView = tuple[str, InlineKeyboardMarkup | None]


def _version_line(reports: Sequence[NodeReport]) -> str | None:
    versions = swarmview.node_versions(reports)
    if not versions:
        return None
    latest = swarmview.latest_version(versions)
    if all(v == latest for v in versions.values()):
        return f"Версия: v{latest}"
    return f"Версия: ≤v{latest} (есть отставшие — см. «Проверить обновления»)"


def _unique_service_count(reports: Sequence[NodeReport]) -> int:
    names = {
        s.get("name")
        for r in reports
        if r.state is not None
        for s in r.state.get("services", [])
        if s.get("name")
    }
    return len(names)


def _unique_skill_count(app_actions: Sequence[ActionSpec]) -> int:
    return sum(1 for a in app_actions if not a.params)


def build_panel_view(
    reports: Sequence[NodeReport],
    app_actions: Sequence[ActionSpec],
    now: datetime,
    wake_buttons: Sequence[InlineKeyboardButton] = (),
) -> tuple[str, InlineKeyboardMarkup]:
    total = len(reports)
    online = sum(1 for r in reports if r.alive and r.state is not None)
    lines = [f"{swarmview.SWARM_HEADER}: {total} нод, в сети {online}"]
    version_line = _version_line(reports)
    if version_line:
        lines.append(version_line)
    lines.append(f"Служб: {_unique_service_count(reports)} уникальных")
    lines.append(f"Умений: {_unique_skill_count(app_actions)} уникальных")
    failure = swarmview.last_failure_line(list(reports), now)
    if failure:
        lines.append(failure)

    buttons: list[list[InlineKeyboardButton]] = [
        [button("🧩 Умения", commands.panel_callback(commands.SWARM_SKILLS_CODE, 0))],
        [button("🖥 Ноды", commands.panel_callback(commands.SWARM_NODES_CODE, 0))],
        [button("🔄 Проверить обновления", commands.panel_callback(commands.SWARM_UPDATE_CODE))],
    ]
    buttons.extend([b] for b in wake_buttons)
    buttons.append(refresh_row(commands.panel_callback(commands.SWARM_PANEL_CODE)))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


def _visible_skills(app_actions: Sequence[ActionSpec], subscription: Subscription) -> list[ActionSpec]:
    return [a for a in app_actions if not a.params and subscription.allows_action(a.id, APPS_SERVICE)]


def build_skills_list_view(
    app_actions: Sequence[ActionSpec], subscription: Subscription, offset: int
) -> tuple[str, InlineKeyboardMarkup]:
    skills = _visible_skills(app_actions, subscription)
    lines = [f"🧩 <b>Умения</b> ({len(skills)})", ""]
    if not skills:
        lines.append("Нет доступных умений.")
    page = skills[offset : offset + SKILLS_PAGE_SIZE]
    buttons = [
        [button(action.title, commands.panel_callback(commands.SWARM_SKILL_CODE, action.id))]
        for action in page
    ]
    nav = nav_row(
        offset,
        SKILLS_PAGE_SIZE,
        len(skills),
        lambda o: commands.panel_callback(commands.SWARM_SKILLS_CODE, o),
    )
    if nav:
        buttons.append(nav)
    buttons.append(back_row(commands.panel_callback(commands.SWARM_PANEL_CODE)))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


def build_skill_card_view(
    text: str, keyboard: InlineKeyboardMarkup | None
) -> tuple[str, InlineKeyboardMarkup]:
    rows = list(keyboard.inline_keyboard) if keyboard is not None else []
    rows.append(back_row(commands.panel_callback(commands.SWARM_SKILLS_CODE, 0)))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def build_nodes_list_view(
    reports: Sequence[NodeReport],
    offset: int,
    wakeable_ids: Sequence[str],
    own_node_id: str | None,
) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"🖥 <b>Ноды</b> ({len(reports)})", ""]
    ordered = swarmview.order_by_health(reports)
    page = ordered[offset : offset + NODES_PAGE_SIZE]
    lines.extend(swarmview.node_line(r) for r in page)
    wakeable = set(wakeable_ids)

    buttons: list[list[InlineKeyboardButton]] = []
    for r in page:
        segment = nodeview.node_segment(None if r.node_id == own_node_id else r.node_id)
        buttons.append([button(f"🖥 {r.node_id}", commands.panel_callback(commands.SWARM_NODE_CODE, segment))])
        if r.node_id in wakeable:
            buttons.append([button(swarmview.wake_node_text(r.node_id), commands.wake_callback(r.node_id))])
    nav = nav_row(
        offset,
        NODES_PAGE_SIZE,
        len(reports),
        lambda o: commands.panel_callback(commands.SWARM_NODES_CODE, o),
    )
    if nav:
        buttons.append(nav)
    buttons.append(refresh_row(commands.panel_callback(commands.SWARM_NODES_CODE, offset)))
    buttons.append(back_row(commands.panel_callback(commands.SWARM_PANEL_CODE)))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


def build_update_result_view(summary_text: str) -> tuple[str, InlineKeyboardMarkup]:
    text = f"🔄 <b>Обновления роя</b>\n\n{summary_text}"
    return text, InlineKeyboardMarkup(
        inline_keyboard=[back_row(commands.panel_callback(commands.SWARM_PANEL_CODE))]
    )


async def check_updates_summary(node_link: ServiceLink) -> str:
    own = await fetch_state(node_link, None)
    if own is None:
        return "недоступно: своя нода не отвечает"
    try:
        own_check = await node_link.command(CHECK_UPDATE_ACTION, {}, dst=None)
    except ProtoError as exc:
        return f"не вышло проверить обновления: {exc.message}"
    except (ServiceUnavailableError, TimeoutError) as exc:
        return f"недоступно: своя нода не ответила ({exc})"
    latest = own_check.get("latest")
    if not latest:
        return "не удалось узнать последнюю версию (сеть?)"

    reports = await collect_reports(node_link, own, with_monitor=False)
    behind: list[str] = []
    restart_only: list[str] = []
    unreachable: list[str] = []
    for r in reports:
        if not r.alive or r.state is None:
            unreachable.append(r.node_id)
            continue
        running = r.state.get("version")
        if running and running != latest:
            behind.append(r.node_id)
            continue
        if (r.state.get("update") or {}).get("restart_required"):
            restart_only.append(r.node_id)

    parts = [f"Последняя версия: v{latest}."]
    if behind:
        parts.append("Нужен update: " + ", ".join(sorted(behind)) + ".")
    if restart_only:
        parts.append(
            "Update уже на диске, нужен только restart_node: " + ", ".join(sorted(restart_only)) + "."
        )
    if unreachable:
        parts.append("Не отвечают, не проверить: " + ", ".join(sorted(unreachable)) + ".")
    if not behind and not restart_only:
        parts.append("Все доступные ноды на последней версии.")
    return " ".join(parts)


async def _collect_reports(node_link: ServiceLink, state: AppState) -> tuple[dict | None, list[NodeReport]]:
    try:
        own_state = await node_link.get_state()
    except PEER_ERRORS:
        return None, []
    reports = await collect_reports(node_link, own_state, with_monitor=True)
    await swarmview.remember_wake_info(state, reports)
    return own_state, reports


async def _wake_buttons(
    subscription: Subscription | None,
    state: AppState,
    wake_config: WakeConfig | None,
    reports: list[NodeReport],
) -> list[InlineKeyboardButton]:
    if subscription is None:
        return []
    return swarmview.wake_rows(subscription, wake_config) + await swarmview.offline_wake_rows(
        subscription, state, reports
    )


async def _panel_view(
    node_link: ServiceLink,
    apps_link: ServiceLink,
    state: AppState,
    wake_config: WakeConfig | None,
    subscription: Subscription | None,
) -> ScreenView:
    own_state, reports = await _collect_reports(node_link, state)
    if own_state is None:
        return nodeview.NODE_DOWN_TEXT, None
    app_actions = await apps_link.actions()
    wake_buttons = await _wake_buttons(subscription, state, wake_config, reports)
    return build_panel_view(reports, app_actions, datetime.now(UTC), wake_buttons)


async def _nodes_view(
    node_link: ServiceLink, state: AppState, subscription: Subscription | None, offset: int
) -> ScreenView:
    own_state, reports = await _collect_reports(node_link, state)
    if own_state is None:
        return nodeview.NODE_DOWN_TEXT, None
    wakeable_ids = (
        await swarmview.offline_wakeable_ids(subscription, state, reports) if subscription is not None else []
    )
    return build_nodes_list_view(reports, offset, wakeable_ids, own_state.get("node"))


async def _render_screen(
    code: str,
    parts: list[str],
    node_link: ServiceLink,
    apps_link: ServiceLink,
    state: AppState,
    wake_config: WakeConfig | None,
    subscription: Subscription | None,
) -> ScreenView | None:
    if code == commands.SWARM_PANEL_CODE:
        return await _panel_view(node_link, apps_link, state, wake_config, subscription)
    if code == commands.SWARM_SKILLS_CODE:
        if subscription is None:
            return None
        app_actions = await apps_link.actions()
        return build_skills_list_view(app_actions, subscription, int_or(part(parts, 2), 0))
    if code == commands.SWARM_SKILL_CODE:
        skill_text, skill_keyboard = await run_app_skill(apps_link, subscription, part(parts, 2) or "")
        return build_skill_card_view(skill_text, skill_keyboard)
    if code == commands.SWARM_NODES_CODE:
        return await _nodes_view(node_link, state, subscription, int_or(part(parts, 2), 0))
    node_id = nodeview.node_from_segment(part(parts, 2))
    if code == commands.SWARM_NODE_CODE:
        return await nodeview.build_node_card_view(node_link, subscription, node_id)
    if code == commands.SWARM_SVCS_CODE:
        return await nodeview.build_services_page_view(
            node_link, subscription, node_id, int_or(part(parts, 3), 0)
        )
    if code == commands.SWARM_SVC_CODE:
        return await nodeview.build_service_card_view(node_link, subscription, part(parts, 3) or "", node_id)
    return None


@router.message(Command("swarm", "nodes"))
async def cmd_swarm(
    message: Message,
    node_link: ServiceLink,
    apps_link: ServiceLink,
    state: AppState,
    wake_config: WakeConfig | None = None,
    subscription: Subscription | None = None,
) -> None:
    text, keyboard = await _panel_view(node_link, apps_link, state, wake_config, subscription)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith(f"{commands.CALLBACK_PREFIX}:sw_"))
async def on_swarm_screen(
    callback: CallbackQuery,
    node_link: ServiceLink,
    apps_link: ServiceLink,
    state: AppState,
    wake_config: WakeConfig | None = None,
    subscription: Subscription | None = None,
) -> None:
    parts = (callback.data or "").split(":")
    code = part(parts, 1) or ""

    if code == commands.SWARM_UPDATE_CODE:
        await callback.answer("Проверяю обновления…")
        summary = await check_updates_summary(node_link)
        text, keyboard = build_update_result_view(summary)
        await redraw(callback, text, keyboard)
        return

    if code == commands.SWARM_SKILL_CODE and (
        subscription is None or not subscription.allows_action(part(parts, 2) or "", APPS_SERVICE)
    ):
        await callback.answer("⛔️ Недоступно", show_alert=True)
        return

    view = await _render_screen(code, parts, node_link, apps_link, state, wake_config, subscription)
    if view is not None:
        await redraw(callback, *view)
    await callback.answer()
