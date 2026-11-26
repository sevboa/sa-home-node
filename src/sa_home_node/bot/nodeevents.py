from __future__ import annotations

import html
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from aiogram.types import InlineKeyboardMarkup

from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.broadcast import broadcast_system, notify_admins
from sa_home_node.bot.commands import action_callback
from sa_home_node.bot.notifier import Notifier
from sa_home_node.bot.spec import EventRoute
from sa_home_node.bot.store_events import EventStore
from sa_home_node.bot.ui import single_button
from sa_home_node.proto.wire import NODE_SERVICE, Envelope
from sa_home_node.services.events import (
    EVENT_SINGLETON_ACTIVATED,
    EVENT_SINGLETON_YIELDED,
    EVENT_UPDATE_FINISHED,
)
from sa_home_node.services.slot import utc_now
from sa_home_node.swarm.events import (
    EVENT_NODE_DOWN,
    EVENT_NODE_JOINED,
    EVENT_NODE_LEAVING,
    EVENT_NODE_RETURNED,
    EVENT_NODE_UP,
    EVENT_RESTART_APPLIED,
)

if TYPE_CHECKING:
    from sa_home_node.bot.context import BotContext

log = logging.getLogger(__name__)

EVENT_IDLE_POWER_BLOCKED = "idle_power_blocked"

ACTION_CLOSE_SSH_SESSIONS = "close_ssh_sessions"

_NODE_STATE_EVENTS = (EVENT_NODE_DOWN, EVENT_NODE_UP, EVENT_NODE_LEAVING, EVENT_NODE_RETURNED)
_SINGLETON_EVENTS = (EVENT_SINGLETON_ACTIVATED, EVENT_SINGLETON_YIELDED)
_VERSION_EVENTS = (EVENT_UPDATE_FINISHED, EVENT_RESTART_APPLIED)

_IDLE_DEDUP_S = 60.0


def _human_downtime(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"


def _seen_recently(recent: dict[str, float], key: str) -> bool:
    now = time.monotonic()
    cutoff = now - _IDLE_DEDUP_S
    for known in list(recent.keys()):
        if recent[known] < cutoff:
            del recent[known]
    if key in recent:
        return True
    recent[key] = now
    return False


def render_node_joined(node_id: str, endpoint: str) -> str:
    return f"🕸 К рою присоединилась нода «{node_id}» ({endpoint})."


def render_node_down(node_id: str, down_s: float) -> str:
    return (
        f"🔌 Нода «{node_id}» не отвечает уже {_human_downtime(down_s)} — "
        f"а эта машина должна быть в сети всегда."
    )


def render_node_up(node_id: str) -> str:
    return f"✅ Нода «{node_id}» снова на связи."


def render_node_leaving(node_id: str) -> str:
    return f"🌙 Нода «{node_id}» выключается штатно."


def render_node_returned(node_id: str) -> str:
    return f"👋 Нода «{node_id}» вернулась в строй."


def render_singleton_activated(slot: str, node_id: str) -> str:
    return f"🎛 Службу «{slot}» приняла нода «{node_id}»."


def render_singleton_yielded(slot: str, node_id: str) -> str:
    return f"🎛 Нода «{node_id}» уступила службу «{slot}» — вернулась основная."


def render_update_finished(node_id: str, ok: bool, version: str | None, error: str | None) -> str:
    if ok:
        return f"⬆️ Нода «{node_id}» обновлена до v{version} — нужен перезапуск (nodectl restart_node)."
    return f"⚠️ Обновление ноды «{node_id}» не удалось: {html.escape(error or '')}"


def render_restart_applied(node_id: str, from_version: str | None, to_version: str) -> str:
    if from_version:
        return f"🔄 Нода «{node_id}» перезапустилась на v{to_version} (была v{from_version})."
    return f"🔄 Нода «{node_id}» на связи, версия v{to_version}."


def render_idle_power_blocked(node_id: str, sessions: list[str], persona: str = "server") -> str:
    lines = "\n".join(f"• {html.escape(s)}" for s in sessions)
    return (
        f"⚠️ Нода «{node_id}» простаивает ({persona} заснул по тайм-ауту), но не "
        f"выключаюсь — открыта SSH-сессия:\n{lines}\n"
        "Возможно, кто-то работает за машиной."
    )


def build_close_ssh_keyboard(node_id: str) -> InlineKeyboardMarkup:
    return single_button(
        "🔌 Закрыть SSH-сессии и выключить",
        action_callback(ACTION_CLOSE_SSH_SESSIONS, node=node_id, service=NODE_SERVICE),
    )


async def _record_and_notify_admins(
    book: SubscriptionBook,
    notifier: Notifier,
    store: EventStore,
    name: str,
    node_id: str | None,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    await store.record_swarm_event(name, node_id, text, utc_now())
    await notify_admins(book, notifier, text, event_type=name, reply_markup=reply_markup)


def _render_swarm_event(name: str, data: dict[str, Any], env: Envelope) -> tuple[str, str] | None:
    if name == EVENT_NODE_JOINED:
        node_id = data.get("node_id")
        if not node_id:
            return None
        return render_node_joined(node_id, data.get("endpoint") or "?"), node_id
    if name in _NODE_STATE_EVENTS:
        node_id = data.get("node")
        if not node_id:
            return None
        if name == EVENT_NODE_DOWN:
            return render_node_down(node_id, float(data.get("down_s") or 0)), node_id
        if name == EVENT_NODE_UP:
            return render_node_up(node_id), node_id
        if name == EVENT_NODE_LEAVING:
            return render_node_leaving(node_id), node_id
        return render_node_returned(node_id), node_id
    if name in _SINGLETON_EVENTS:
        slot, node_id = data.get("slot"), data.get("node")
        if not slot or not node_id:
            return None
        if name == EVENT_SINGLETON_ACTIVATED:
            return render_singleton_activated(slot, node_id), node_id
        return render_singleton_yielded(slot, node_id), node_id
    if name in _VERSION_EVENTS:
        if env.src is None or not env.src.node:
            return None
        node_id = env.src.node
        if name == EVENT_UPDATE_FINISHED:
            text = render_update_finished(
                node_id, bool(data.get("ok")), data.get("version"), data.get("error")
            )
            return text, node_id
        to_version = data.get("to")
        if not to_version:
            return None
        return render_restart_applied(node_id, data.get("from"), to_version), node_id
    return None


def build_node_event_handler(
    ctx: BotContext, routes: tuple[EventRoute, ...], about_name: str = "server"
) -> Callable[[Envelope], Awaitable[None]]:
    book: SubscriptionBook = ctx.book
    notifier: Notifier = ctx.notifier
    events: EventStore = ctx.events

    recent_idle: dict[str, float] = {}

    route_map: dict[str, Callable[[BotContext, Envelope], Awaitable[None]]] = {}
    for route in routes:
        for name in route.names:
            route_map[name] = route.handle

    async def handle_idle_power_blocked(data: dict[str, Any]) -> None:
        node_id = data.get("node")
        sessions = data.get("sessions") or []
        if not node_id or not sessions:
            return
        key = f"idle:{node_id}:{json.dumps(sessions, sort_keys=True)}"
        if _seen_recently(recent_idle, key):
            return
        await _record_and_notify_admins(
            book,
            notifier,
            events,
            EVENT_IDLE_POWER_BLOCKED,
            node_id,
            render_idle_power_blocked(node_id, sessions, about_name),
            reply_markup=build_close_ssh_keyboard(node_id),
        )

    async def handle(env: Envelope) -> None:
        name = env.payload.get("event")
        data = env.payload.get("data", {})
        if name == EVENT_IDLE_POWER_BLOCKED:
            await handle_idle_power_blocked(data)
        else:
            rendered = _render_swarm_event(name, data, env)
            if rendered is None:
                handler = route_map.get(name)
                if handler is not None:
                    await handler(ctx, env)
                return
            text, node_id = rendered
            await events.record_swarm_event(name, node_id, text, utc_now())
            await broadcast_system(book, notifier, text)

    return handle
