from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from sa_home_node.bot.config import GuestSubscriptionConfig, SubscriptionConfig
from sa_home_node.bot.retry import REQUEST_TIMEOUT_S, call_with_network_retry
from sa_home_node.bot.subs import SOURCE_GUEST, Subscription

log = logging.getLogger(__name__)

RETIRED_RIGHTS = frozenset({"tell_guests@llm"})
NO_TAGS: Mapping[int, frozenset[str]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    chat_id: int
    name: str
    reason: str


def config_subscription(cfg: SubscriptionConfig) -> Subscription:
    return Subscription(
        name=cfg.name,
        chat_id=cfg.chat_id,
        event_types=frozenset(cfg.event_types),
        except_event_types=frozenset(cfg.except_event_types),
        allowed_commands=frozenset(cfg.allowed_commands),
    )


def guest_subscription(cfg: GuestSubscriptionConfig) -> Subscription:
    return Subscription(
        name=cfg.name,
        chat_id=cfg.chat_id,
        event_types=frozenset(cfg.event_types),
        allowed_commands=frozenset(cfg.allowed_commands) - RETIRED_RIGHTS,
        source=SOURCE_GUEST,
        invited_by_chat_id=cfg.invited_by_chat_id,
        invited_at=cfg.invited_at,
        invited_user=cfg.invited_user,
    )


class SubscriptionBook:
    def __init__(
        self, subs: Iterable[Subscription] = (), tags: Mapping[int, frozenset[str]] = NO_TAGS
    ) -> None:
        self._tags = tags
        self._subs: list[Subscription] = []
        for sub in subs:
            self.add(sub)

    @classmethod
    def from_config(
        cls,
        configs: Iterable[SubscriptionConfig],
        guests: Iterable[GuestSubscriptionConfig],
        tags: Mapping[int, frozenset[str]] = NO_TAGS,
    ) -> SubscriptionBook:
        subs = [config_subscription(c) for c in configs]
        known = {s.chat_id for s in subs}
        for guest in guests:
            if guest.chat_id in known:
                log.warning(
                    "Гостевая подписка chat_id=%s игнорируется — этот чат есть в конфиге", guest.chat_id
                )
                continue
            subs.append(guest_subscription(guest))
        return cls(subs, tags)

    def all(self) -> list[Subscription]:
        return list(self._subs)

    def guests(self) -> list[Subscription]:
        return [sub for sub in self._subs if sub.is_guest]

    def for_chat(self, chat_id: int) -> Subscription | None:
        return next((sub for sub in self._subs if sub.chat_id == chat_id), None)

    def accepting(self, event_type: str) -> list[Subscription]:
        return [sub for sub in self._subs if sub.accepts_event(event_type)]

    def add(self, sub: Subscription) -> None:
        self.remove(sub.chat_id)
        self._subs.append(replace(sub, tags=self._tags.get(sub.chat_id, sub.tags)))

    def remove(self, chat_id: int) -> bool:
        before = len(self._subs)
        self._subs = [sub for sub in self._subs if sub.chat_id != chat_id]
        return len(self._subs) != before

    async def validate_on_startup(self, bot: Bot) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for sub in list(self._subs):
            try:
                await call_with_network_retry(
                    lambda sub=sub: bot.get_chat(sub.chat_id, request_timeout=REQUEST_TIMEOUT_S),
                    what=f"get_chat({sub.chat_id})",
                    attempts=2,
                    delay_s=3.0,
                )
            except TelegramAPIError as exc:
                broken = sub.with_broken()
                self._subs = [broken if s.chat_id == sub.chat_id else s for s in self._subs]
                issues.append(ValidationIssue(sub.chat_id, sub.name, str(exc)))
                log.warning(
                    "Подписка '%s' (chat_id=%s) недоступна: %s — помечена broken", sub.name, sub.chat_id, exc
                )
        return issues
