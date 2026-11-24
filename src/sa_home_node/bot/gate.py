from __future__ import annotations

import logging
import re
import secrets
import time
from collections.abc import Hashable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.broadcast import notify_admins
from sa_home_node.bot.config import InvitesConfig
from sa_home_node.bot.guestfile import GuestStore
from sa_home_node.bot.notifier import Notifier
from sa_home_node.bot.store_invites import InviteStore
from sa_home_node.bot.subs import SOURCE_GUEST, Subscription

log = logging.getLogger(__name__)

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8
GROUP = 4

_CONFUSABLES = {"I": "1", "L": "1", "O": "0"}
_SEPARATORS = re.compile(r"[\s\-_.]+")
_MAX_RAW_LENGTH = 16


def generate_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def format_code(code: str) -> str:
    return "-".join(code[i : i + GROUP] for i in range(0, len(code), GROUP))


def normalize(text: str | None) -> str | None:
    if not text:
        return None
    raw = text.strip()
    if len(raw) > _MAX_RAW_LENGTH:
        return None
    cleaned = _SEPARATORS.sub("", raw).upper()
    if len(cleaned) != CODE_LENGTH:
        return None
    candidate = "".join(_CONFUSABLES.get(ch, ch) for ch in cleaned)
    if any(ch not in ALPHABET for ch in candidate):
        return None
    return candidate


class AttemptLimiter:
    WINDOW_S = 3600.0

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._hits: dict[Hashable, list[float]] = {}

    def register(self, key: Hashable, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        hits = [t for t in self._hits.get(key, []) if moment - t < self.WINDOW_S]
        hits.append(moment)
        self._hits[key] = hits
        return len(hits) <= self._limit

    def exhausted_now(self, key: Hashable) -> bool:
        return len(self._hits.get(key, [])) == self._limit + 1

    def forget(self, key: Hashable) -> None:
        self._hits.pop(key, None)


@dataclass(frozen=True, slots=True)
class Admission:
    subscription: Subscription
    invited_by_chat_id: int
    code: str


class Gatekeeper:
    def __init__(
        self,
        cfg: InvitesConfig,
        store: InviteStore,
        book: SubscriptionBook,
        guest_store: GuestStore,
        notifier: Notifier,
    ) -> None:
        self._cfg = cfg
        self._store = store
        self._book = book
        self._guest_store = guest_store
        self._notifier = notifier
        self._limiter = AttemptLimiter(cfg.max_attempts_per_hour)

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled and self._guest_store.available

    async def issue(
        self, chat_id: int, user_id: int | None, ttl_s: float | None = None
    ) -> tuple[str, datetime]:
        now = datetime.now(tz=UTC)
        expires = now + timedelta(seconds=ttl_s if ttl_s is not None else self._cfg.ttl_s)
        code = generate_code()
        await self._store.create(code, chat_id, user_id, now, expires)
        log.info("Выпущен инвайт-код (chat=%s, годен до %s)", chat_id, expires.isoformat())
        return code, expires

    async def open_codes(self) -> list[dict[str, Any]]:
        return await self._store.open_codes(datetime.now(tz=UTC))

    async def known_code(self, text: str | None) -> tuple[str, dict[str, Any]] | None:
        code = normalize(text)
        if code is None:
            return None
        row = await self._store.find(code)
        return (code, row) if row is not None else None

    async def revoke_code(self, code: str) -> bool:
        return await self._store.revoke(code, datetime.now(tz=UTC))

    async def try_admit(
        self,
        chat_id: int,
        text: str | None,
        *,
        user_id: int | None = None,
        user_name: str | None = None,
        chat_title: str | None = None,
    ) -> Admission | None:
        if not self.enabled:
            return None
        code = normalize(text)
        if code is None:
            return None
        if not self._limiter.register(chat_id):
            if self._limiter.exhausted_now(chat_id):
                await notify_admins(
                    self._book,
                    self._notifier,
                    f"🚨 Подбор инвайт-кода: chat_id=<code>{chat_id}</code> "
                    f"исчерпал лимит попыток. Отвечать ему я не буду.",
                )
            return None
        now = datetime.now(tz=UTC)
        redeemed = await self._store.redeem(code, chat_id, user_id, user_name, now)
        if not redeemed:
            return None
        self._limiter.forget(chat_id)
        row = await self._store.find(code)
        if row is None:
            return None
        subscription = self._make_guest(chat_id, now, row, user_name, chat_title)
        self._book.add(subscription)
        self._guest_store.save(self._book.guests())
        log.info(
            "Инвайт погашен: chat_id=%s (%s), пригласил chat_id=%s",
            chat_id,
            user_name or "?",
            row.get("issued_by_chat_id"),
        )
        return Admission(
            subscription=subscription,
            invited_by_chat_id=int(row.get("issued_by_chat_id") or 0),
            code=code,
        )

    def _make_guest(
        self,
        chat_id: int,
        now: datetime,
        row: dict[str, Any],
        user_name: str | None,
        chat_title: str | None,
    ) -> Subscription:
        name = chat_title or user_name or f"гость {chat_id}"
        return Subscription(
            name=name,
            chat_id=chat_id,
            event_types=frozenset(self._cfg.grant_events),
            allowed_commands=frozenset(self._cfg.grant_commands),
            source=SOURCE_GUEST,
            invited_by_chat_id=int(row.get("issued_by_chat_id") or 0),
            invited_at=now.isoformat(),
            invited_user=user_name or "",
        )
