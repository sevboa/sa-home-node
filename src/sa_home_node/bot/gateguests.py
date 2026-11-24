from __future__ import annotations

import logging

from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.guestfile import GuestStore
from sa_home_node.bot.subs import Subscription

log = logging.getLogger(__name__)


def revoke_guest(book: SubscriptionBook, guest_store: GuestStore, chat_id: int) -> bool:
    sub = book.for_chat(chat_id)
    if sub is None or not sub.is_guest:
        return False
    book.remove(chat_id)
    guest_store.save(book.guests())
    log.info("Гость chat_id=%s отозван", chat_id)
    return True


def set_guest_rights(
    book: SubscriptionBook, guest_store: GuestStore, chat_id: int, allowed_commands: frozenset[str]
) -> Subscription | None:
    sub = book.for_chat(chat_id)
    if sub is None or not sub.is_guest:
        return None
    if sub.allowed_commands == allowed_commands:
        return sub
    updated = sub.with_allowed_commands(allowed_commands)
    book.add(updated)
    guest_store.save(book.guests())
    return updated


def add_guest_right(
    book: SubscriptionBook, guest_store: GuestStore, chat_id: int, right: str
) -> Subscription | None:
    sub = book.for_chat(chat_id)
    if sub is None:
        return None
    updated = set_guest_rights(book, guest_store, chat_id, sub.allowed_commands | {right})
    if updated is not None:
        log.info("Гостю chat_id=%s выдано право %s", chat_id, right)
    return updated


def remove_guest_right(
    book: SubscriptionBook, guest_store: GuestStore, chat_id: int, right: str
) -> Subscription | None:
    sub = book.for_chat(chat_id)
    if sub is None:
        return None
    updated = set_guest_rights(book, guest_store, chat_id, sub.allowed_commands - {right})
    if updated is not None:
        log.info("У гостя chat_id=%s отозвано право %s", chat_id, right)
    return updated
