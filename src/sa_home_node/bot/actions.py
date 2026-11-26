from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from sa_home_node.bot import commands
from sa_home_node.bot.servicelink import ServiceLink
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.describe import ActionSpec
from sa_home_node.proto.wire import (
    ERR_NEEDS_PRIVILEGE,
    Address,
    ProtoError,
    ServiceUnavailableError,
)

APPS_SERVICE = "apps"

ALREADY_QUEUED_TEXT = "⏳ Уже в очереди — дождитесь результата."

_APP_STATUS = {
    "active": "✅ работает",
    "inactive": "⏹ остановлен",
    "failed": "❌ упал",
}


def unavailable_text(link: ServiceLink) -> str:
    return f"⚠️ Служба «{link.display_name}» недоступна — попробуйте позже."


async def find_action(
    link: ServiceLink, service: str, action_id: str, node_id: str | None = None
) -> ActionSpec | None:
    desc = await link.describe(dst=Address(node=node_id, service=service))
    if desc is None:
        return None
    for action in desc.actions:
        if action.id == action_id:
            return action
    return None


def build_args(action: ActionSpec, value: str | None) -> dict[str, str]:
    if value is None or not action.params:
        return {}
    return {action.params[0].name: value}


def render_result(action: ActionSpec, result: dict[str, object]) -> str:
    flags = [v for v in result.values() if isinstance(v, bool)]
    if flags and not any(flags):
        return ALREADY_QUEUED_TEXT
    return f"✅ Принято: {action.title}"


async def run_action(
    link: ServiceLink,
    service: str,
    action_id: str,
    value: str | None = None,
    node_id: str | None = None,
) -> str:
    dst = Address(node=node_id, service=service)
    action = await find_action(link, service, action_id, node_id=node_id)
    if action is None:
        return unavailable_text(link) if not link.connected else "Действие недоступно."
    try:
        result = await link.command(action.id, build_args(action, value), dst=dst)
    except ServiceUnavailableError:
        return unavailable_text(link)
    except ProtoError as exc:
        return f"⚠️ Ошибка: {exc.message}"
    except (ConnectionError, OSError, TimeoutError):
        return f"⚠️ Служба «{link.display_name}» не ответила вовремя — попробуйте позже."
    return render_result(action, result)


def rows(buttons: list[InlineKeyboardButton]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i : i + 2] for i in range(0, len(buttons), 2)])


def build_choice_keyboard(
    sub: Subscription | None,
    service_actions: Sequence[ActionSpec],
    service: str,
    value: str,
    node_id: str | None = None,
) -> InlineKeyboardMarkup | None:
    if sub is None:
        return None
    buttons = [
        InlineKeyboardButton(
            text=action.title,
            callback_data=commands.action_callback(action.id, value, node_id, service=service),
        )
        for action in service_actions
        if sub.allows_action(action.id, service)
        and action.params
        and action.params[0].choices
        and value in action.params[0].choices
    ]
    return rows(buttons)


def visible_skill_actions(sub: Subscription | None, actions: Sequence[ActionSpec]) -> list[ActionSpec]:
    return [
        action
        for action in actions
        if not action.params and sub is not None and sub.allows_action(action.id, APPS_SERVICE)
    ]


def render_app_card(app: dict[str, object]) -> str:
    status = _APP_STATUS.get(str(app.get("status", "")), f"❔ {app.get('status', '?')}")
    lines = [
        f"{app.get('title', app.get('id', '?'))} — {status}",
        f"Юнит: <code>{app.get('unit', '?')}</code>",
    ]
    urls = app.get("urls") or []
    if len(urls) == 1:
        lines.append(f"Веб-морда: {urls[0]}")
    elif urls:
        lines.append("Веб-морда:")
        lines.extend(urls)
    return "\n".join(lines)


async def run_app_skill(
    apps_link: ServiceLink,
    sub: Subscription | None,
    action_id: str,
    value: str | None = None,
) -> tuple[str, InlineKeyboardMarkup | None]:
    action = next((a for a in await apps_link.actions() if a.id == action_id), None)
    if action is None:
        text = unavailable_text(apps_link) if not apps_link.connected else "Умение недоступно."
        return text, None
    try:
        result = await apps_link.command(action.id, build_args(action, value))
    except ServiceUnavailableError:
        return unavailable_text(apps_link), None
    except ProtoError as exc:
        if exc.code == ERR_NEEDS_PRIVILEGE:
            return f"⚠️ {exc.message}", None
        return f"⚠️ Ошибка: {exc.message}", None

    app_id = result.get("id") or value or action_id
    apps_actions = await apps_link.actions()
    keyboard = build_choice_keyboard(sub, apps_actions, APPS_SERVICE, app_id)
    return render_app_card(result), keyboard
