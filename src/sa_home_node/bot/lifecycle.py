from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.broadcast import broadcast_system
from sa_home_node.bot.db import AppState
from sa_home_node.bot.notifier import Notifier
from sa_home_node.bot.render_alerts import render_outage_line
from sa_home_node.bot.ui import format_duration
from sa_home_node.monitor.models import POWER_UNEXPECTED, PowerEvent

log = logging.getLogger(__name__)

ReadLastOutage = Callable[[], Awaitable[PowerEvent | None]]


def render_startup(clean: bool, last_outage: PowerEvent | None = None) -> str:
    if clean:
        return "🟢 <b>Сторож снова на посту.</b>\nЗапуск после штатного завершения."
    if last_outage is not None and last_outage.kind == POWER_UNEXPECTED:
        return (
            "🟠 <b>Сторож восстановился после сбоя.</b>\n"
            "Похоже, была потеря питания или зависание машины.\n\n"
            "Последнее отключение:\n" + render_outage_line(last_outage)
        )
    return (
        "🟠 <b>Сторож восстановился после сбоя.</b>\n"
        "Предыдущая сессия завершилась нештатно (краш или потеря питания)."
    )


def render_shutdown() -> str:
    return "⚫️ <b>Сторож уходит в офлайн.</b>\nШтатное завершение — мониторинг приостановлен."


def render_link_restored(seconds: float) -> str:
    return f"🔌 <b>Связь с Telegram восстановлена.</b>\nБыли офлайн ~{format_duration(seconds)}."


async def announce_startup(
    book: SubscriptionBook,
    notifier: Notifier,
    state: AppState,
    read_last_outage: ReadLastOutage,
) -> bool:
    clean = await state.get("last_shutdown_clean") in (None, "1")
    await state.set("last_shutdown_clean", "0")
    if clean:
        text = render_startup(True)
    else:
        last_outage = await read_last_outage()
        text = render_startup(False, last_outage)
    sent = await broadcast_system(book, notifier, text)
    log.info("Системное событие разослано %d подписчикам", sent)
    return clean


async def announce_shutdown(book: SubscriptionBook, notifier: Notifier, state: AppState) -> None:
    sent = await broadcast_system(book, notifier, render_shutdown())
    log.info("Системное событие разослано %d подписчикам", sent)
    await state.set("last_shutdown_clean", "1")
