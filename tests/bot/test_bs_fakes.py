from __future__ import annotations

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import GetMe
from fakebot import FakeBot, make_error, record_sleeps
from fakelink import FakeLink

from sa_home_node.proto.wire import ServiceUnavailableError

SEND = GetMe()


async def test_calls_in_order_and_message_ids():
    bot = FakeBot()
    first = await bot.send_message(7, "а", parse_mode="HTML")
    second = await bot.send_document(7, "d")
    await bot.delete_message(7, first.message_id)
    assert (first.message_id, second.message_id) == (1, 2)
    assert bot.calls == [
        ("send_message", {"chat_id": 7, "text": "а", "parse_mode": "HTML"}),
        ("send_document", {"chat_id": 7, "document": "d"}),
        ("delete_message", {"chat_id": 7, "message_id": 1}),
    ]


@pytest.mark.parametrize(
    "error",
    [
        TelegramRetryAfter(SEND, "flood", 3),
        TelegramBadRequest(SEND, "bad"),
        TelegramForbiddenError(SEND, "forbidden"),
        TelegramNetworkError(SEND, "net"),
    ],
)
async def test_script_fires_once(error):
    bot = FakeBot()
    bot.script["send_message"] = [error]
    with pytest.raises(type(error)):
        await bot.send_message(1, "x")
    assert (await bot.send_message(1, "y")).message_id == 1
    assert len(bot.of("send_message")) == 2


async def test_call_through_aiogram_message():
    from datetime import datetime

    from aiogram.types import Chat, Message

    bot = FakeBot()
    message = Message(message_id=5, date=datetime(2026, 1, 1), chat=Chat(id=1, type="private")).as_(bot)
    await message.edit_text("т")
    assert bot.calls == [("edit_message_text", {"text": "т", "chat_id": 1, "message_id": 5})]


def test_make_error_and_sleeps(monkeypatch):
    assert isinstance(make_error("ra:3", "x"), TelegramRetryAfter)
    assert make_error("ra:3").retry_after == 3
    with pytest.raises(ValueError):
        make_error("zzz")
    assert record_sleeps(monkeypatch) == []


async def test_link_replies_and_unavailable():
    link = FakeLink({(None, "get_state"): {"ok": 1}, ("n1", "ping"): {"pong": True}})
    assert await link.get_state() == {"ok": 1}
    assert await link.command("ping", dst="n1") == {"pong": True}
    link.replies[(None, "get_state")] = ServiceUnavailableError("down")
    with pytest.raises(ServiceUnavailableError):
        await link.get_state()
    with pytest.raises(ServiceUnavailableError):
        await link.command("none")
    assert [name for name, _ in link.calls] == ["get_state", "ping", "get_state", "none"]
