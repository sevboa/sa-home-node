from __future__ import annotations

import asyncio
import hashlib
import re
from types import SimpleNamespace
from typing import Any

from aiogram.client.default import Default
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
)
from aiogram.methods import GetMe
from aiogram.types import InputFile
from pydantic import BaseModel

SIGNATURES: dict[str, tuple[str, ...]] = {
    "send_message": ("chat_id", "text"),
    "edit_message_text": ("text",),
    "edit_message_reply_markup": (),
    "edit_message_caption": (),
    "send_document": ("chat_id", "document"),
    "send_photo": ("chat_id", "photo"),
    "send_voice": ("chat_id", "voice"),
    "send_media_group": ("chat_id", "media"),
    "delete_message": ("chat_id", "message_id"),
    "get_chat": ("chat_id",),
    "set_my_commands": ("commands",),
    "delete_my_commands": (),
    "get_me": (),
    "send_chat_action": ("chat_id", "action"),
}

SENDING = frozenset(
    {"send_message", "edit_message_text", "edit_message_reply_markup", "edit_message_caption"}
    | {"send_document", "send_photo", "send_voice"}
)


def make_error(token: str, tag: str = "") -> Exception:
    kind, _, arg = token.partition(":")
    method = GetMe()
    if kind == "net":
        return TelegramNetworkError(method, f"net {tag}")
    if kind == "ra":
        return TelegramRetryAfter(method, f"flood {tag}", int(arg))
    if kind == "bad":
        return TelegramBadRequest(method, f"bad {tag}")
    if kind == "bad_parse":
        return TelegramBadRequest(method, f"can't parse entities: {tag}")
    if kind == "forbidden":
        return TelegramForbiddenError(method, f"forbidden {tag}")
    if kind == "notfound":
        return TelegramNotFound(method, f"notfound {tag}")
    if kind == "runtime":
        return RuntimeError(f"runtime {tag}")
    raise ValueError(token)


def record_sleeps(monkeypatch: Any) -> list[float]:
    sleeps: list[float] = []

    async def fake_sleep(delay: float, *args: Any, **kwargs: Any) -> None:
        sleeps.append(float(delay))

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return sleeps


def norm(value: Any) -> Any:
    if isinstance(value, InputFile):
        return {"file": value.filename, "size": len(getattr(value, "data", b""))}
    if isinstance(value, BaseModel):
        fields = type(value).model_fields
        return {
            name: norm(item)
            for name in fields
            if (item := getattr(value, name)) is not None and not isinstance(item, Default)
        }
    if isinstance(value, str) and len(value) > 200:
        return f"<{len(value)}:{hashlib.sha256(value.encode()).hexdigest()[:12]}>"
    if isinstance(value, (bytes, bytearray)):
        return {"bytes": len(value)}
    if isinstance(value, dict):
        return {key: norm(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [norm(item) for item in value]
    return value


class OutboxLog:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def enqueue(
        self,
        chat_id: int,
        kind: str,
        payload: dict[str, Any],
        now: Any = None,
        dedup_key: str | None = None,
        message_thread_id: int | None = None,
    ) -> int:
        self.rows.append(
            {
                "chat_id": chat_id,
                "kind": kind,
                "payload": norm(payload),
                "dedup_key": dedup_key,
                "thread": message_thread_id,
            }
        )
        return len(self.rows)


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.script: dict[str, list[BaseException | None]] = {}
        self._next_id = 1

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def of(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for call, kwargs in self.calls if call == name]

    def dump(self) -> list[list[Any]]:
        return [
            [name, norm({k: v for k, v in kwargs.items() if v is not None or k == "parse_mode"})]
            for name, kwargs in self.calls
        ]

    def _message(self) -> SimpleNamespace:
        number = self._next_id
        message = SimpleNamespace(
            message_id=number,
            document=SimpleNamespace(file_id=f"doc-{number}"),
            photo=[SimpleNamespace(file_id=f"photo-{number}")],
        )
        self._next_id += 1
        return message

    def _record(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        bound = dict(zip(SIGNATURES[name], args, strict=False))
        self.calls.append((name, {**bound, **kwargs}))
        queue = self.script.get(name)
        if queue:
            error = queue.pop(0)
            if error is not None:
                raise error

    async def _invoke(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        self._record(name, args, kwargs)
        if name in SENDING:
            return self._message()
        if name == "send_media_group":
            return [self._message() for _ in kwargs.get("media") or args[1:2][0]]
        if name == "get_chat":
            return SimpleNamespace(id=(args or (kwargs.get("chat_id"),))[0])
        if name == "get_me":
            return SimpleNamespace(id=1, username="fakebot", is_bot=True)
        return True

    async def send_message(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("send_message", args, kwargs)

    async def edit_message_text(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("edit_message_text", args, kwargs)

    async def edit_message_reply_markup(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("edit_message_reply_markup", args, kwargs)

    async def edit_message_caption(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("edit_message_caption", args, kwargs)

    async def send_document(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("send_document", args, kwargs)

    async def send_photo(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("send_photo", args, kwargs)

    async def send_voice(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("send_voice", args, kwargs)

    async def send_media_group(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("send_media_group", args, kwargs)

    async def delete_message(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("delete_message", args, kwargs)

    async def get_chat(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("get_chat", args, kwargs)

    async def set_my_commands(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("set_my_commands", args, kwargs)

    async def delete_my_commands(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("delete_my_commands", args, kwargs)

    async def get_me(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("get_me", args, kwargs)

    async def send_chat_action(self, *args: Any, **kwargs: Any) -> Any:
        return await self._invoke("send_chat_action", args, kwargs)

    async def __call__(self, method: Any, request_timeout: Any = None) -> Any:
        name = snake(type(method).__name__)
        fields = {
            key: value
            for key in type(method).model_fields
            if (value := getattr(method, key)) is not None and not isinstance(value, Default)
        }
        return await self._invoke(name, (), fields)
