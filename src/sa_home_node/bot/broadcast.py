from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.subs import WILDCARD, Subscription
from sa_home_node.monitor.models import EVENT_SYSTEM

if TYPE_CHECKING:
    from sa_home_node.bot.notifier import Notifier


async def broadcast(subs: Iterable[Subscription], notifier: Notifier, text: str, **send_kwargs: Any) -> int:
    delivered = 0
    for sub in subs:
        result = await notifier.send_direct(sub.chat_id, text, **send_kwargs)
        if result is not None:
            delivered += 1
    return delivered


def admin_subs(book: SubscriptionBook) -> list[Subscription]:
    return [sub for sub in book.all() if WILDCARD in sub.allowed_commands]


async def notify_admins(
    book: SubscriptionBook,
    notifier: Notifier,
    text: str,
    *,
    reply_markup: Any = None,
    event_type: str | None = None,
) -> None:
    subs = [sub for sub in admin_subs(book) if event_type not in sub.except_event_types]
    await broadcast(subs, notifier, text, reply_markup=reply_markup)


async def broadcast_system(book: SubscriptionBook, notifier: Notifier, text: str) -> int:
    return await broadcast(book.accepting(EVENT_SYSTEM), notifier, text)


async def broadcast_all(book: SubscriptionBook, notifier: Notifier, text: str) -> int:
    return await broadcast([sub for sub in book.all() if not sub.broken], notifier, text)
