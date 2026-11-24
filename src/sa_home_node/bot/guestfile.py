from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from pathlib import Path

from sa_home_node.bot.subs import Subscription
from sa_home_node.files import atomic_write_bytes
from sa_home_node.toml_emit import toml_value

log = logging.getLogger(__name__)

HEADER = (
    "# Гостевые подписки бота — ФАЙЛ ВЕДЁТ САМ БОТ.\n"
    "#\n"
    "# Записи появляются здесь, когда кто-то гасит инвайт-код (/invite), и\n"
    "# исчезают при отзыве (/guests). Файл перезаписывается целиком, поэтому\n"
    "# ручные правки и комментарии в нём не сохранятся — владельческие подписки\n"
    "# правьте в основном пакете instances/telegram-bot.<инстанс>.toml.\n"
    "#\n"
    "# Реплицируется по рою наравне с основным пакетом (сателлит), так что гости\n"
    "# переживают переезд бота на резервную ноду.\n"
)

_FIELDS = (
    "name",
    "chat_id",
    "event_types",
    "allowed_commands",
    "invited_by_chat_id",
    "invited_at",
    "invited_user",
)


def _entry(guest: Subscription) -> str:
    values = {field: getattr(guest, field) for field in _FIELDS}
    for key in ("event_types", "allowed_commands"):
        values[key] = sorted(values[key])
    return "\n".join(
        ["[[guest_subscriptions]]", *(f"{field} = {toml_value(values[field])}" for field in _FIELDS)]
    )


def render(guests: Iterable[Subscription]) -> bytes:
    parts = [_entry(g) for g in sorted(guests, key=lambda g: g.chat_id)]
    if not parts:
        return HEADER.encode("utf-8")
    return (HEADER + "\n" + "\n\n".join(parts) + "\n").encode("utf-8")


class GuestStore:
    def __init__(self, path: Path | None) -> None:
        self._path = path

    @property
    def available(self) -> bool:
        return self._path is not None

    def save(self, guests: Sequence[Subscription]) -> bool:
        if self._path is None:
            return False
        atomic_write_bytes(self._path, render(guests))
        log.info("Гостевой пакет %s переписан: %d записей", self._path, len(guests))
        return True
