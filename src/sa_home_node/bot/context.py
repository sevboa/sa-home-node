from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiogram import Bot

from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.commands import Registry
from sa_home_node.bot.config import BotConfig
from sa_home_node.bot.db import AppState
from sa_home_node.bot.gate import Gatekeeper
from sa_home_node.bot.guestfile import GuestStore
from sa_home_node.bot.notifier import Notifier
from sa_home_node.bot.rights import RightsCatalog
from sa_home_node.bot.servicelink import ServiceLink
from sa_home_node.bot.store_events import EventStore
from sa_home_node.content import ContentStore
from sa_home_node.db import Database
from sa_home_node.wake.ready import WakeGuard

GuestUsage = Callable[[int], Awaitable[str]]
OnAdmitted = Callable[..., Awaitable[None]]


async def no_guest_usage(chat_id: int) -> str:
    return "Учёт использования не ведётся."


@dataclass
class BotContext:
    config: BotConfig
    bot: Bot
    bot_username: str
    book: SubscriptionBook
    notifier: Notifier
    events: EventStore
    app_state: AppState
    db: Database
    node_link: ServiceLink
    monitor_link: ServiceLink
    apps_link: ServiceLink
    registry: Registry
    catalog: RightsCatalog
    guest_store: GuestStore
    gate: Gatekeeper
    help_lines: tuple[str, ...] = ()
    guest_usage: GuestUsage = no_guest_usage
    on_admitted: OnAdmitted | None = None
    wake_guard: WakeGuard | None = None
    content: ContentStore | None = None
