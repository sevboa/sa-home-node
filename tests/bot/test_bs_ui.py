from __future__ import annotations

from datetime import datetime

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Chat, InputRichMessage, Message, User
from botfix import load
from fakebot import FakeBot, make_error

from sa_home_node.bot import ui

T = load("ui/primitives.json")


def dump(buttons) -> list[dict]:
    return [b.model_dump(exclude_none=True) for b in buttons]


def callback(bot: FakeBot, *, with_message: bool = True) -> CallbackQuery:
    message = Message(message_id=5, date=datetime(2026, 1, 1), chat=Chat(id=1, type="private")).as_(bot)
    return CallbackQuery(
        id="c",
        from_user=User(id=1, is_bot=False, first_name="u"),
        chat_instance="x",
        message=message if with_message else None,
    )


def test_texts():
    assert (ui.BACK_TEXT, ui.REFRESH_TEXT) == (T["constants"]["BACK_TEXT"], T["constants"]["REFRESH_TEXT"])


@pytest.mark.parametrize("case", T["pack"], ids=lambda c: repr(c["args"]))
def test_pack(case):
    assert ui.pack(*case["args"]) == case["out"]


@pytest.mark.parametrize("case", T["unpack"], ids=lambda c: repr(c["data"]))
def test_unpack(case):
    result = ui.unpack(case["data"], case["prefix"], case["parts"])
    assert (list(result) if result is not None else None) == case["out"]


@pytest.mark.parametrize("case", T["id_and_button"], ids=lambda c: repr(c["data"]))
def test_unpack_id_and_button(case):
    result = ui.unpack_id_and_button(case["data"], case["prefix"])
    assert (list(result) if result is not None else None) == case["out"]


@pytest.mark.parametrize("case", T["int_or"], ids=lambda c: repr(c["raw"]))
def test_int_or(case):
    assert ui.int_or(case["raw"], case["default"]) == case["out"]


@pytest.mark.parametrize("case", T["part"], ids=lambda c: repr(c))
def test_part(case):
    assert ui.part(case["parts"], case["index"]) == case["out"]


def test_buttons_and_markup():
    assert ui.button("Текст", "cb:1").model_dump(exclude_none=True) == T["button"]
    rows = ([ui.button("a", "1"), ui.button("b", "2")], [ui.button("c", "3")])
    assert ui.markup(*rows).model_dump(exclude_none=True) == T["markup"]
    assert ui.markup().model_dump(exclude_none=True) == T["markup_empty"]
    assert ui.single_button("s", "d").model_dump(exclude_none=True) == T["single_button"]
    assert dump(ui.back_row("back:1")) == T["back_row"]
    assert dump(ui.back_row("back:2", "Домой")) == T["back_row_custom"]
    assert dump(ui.refresh_row("ref:1")) == T["refresh_row"]


@pytest.mark.parametrize("case", T["nav"], ids=lambda c: f"{c['offset']}-{c['size']}-{c['total']}")
def test_nav_row(case):
    row = ui.nav_row(case["offset"], case["size"], case["total"], lambda o: f"pg:{o}")
    assert dump(row) == case["out"]


@pytest.mark.parametrize("case", T["clamp"], ids=lambda c: f"{c['offset']}-{c['size']}-{c['total']}")
def test_clamp_offset(case):
    assert ui.clamp_offset(case["offset"], case["size"], case["total"]) == case["out"]


async def test_redraw_edits_message():
    bot = FakeBot()
    markup = ui.single_button("a", "b")
    await ui.redraw(callback(bot), "новый", markup)
    assert bot.calls == [
        ("edit_message_text", {"text": "новый", "chat_id": 1, "message_id": 5, "reply_markup": markup})
    ]


async def test_redraw_ignores_bad_request_and_missing_message():
    bot = FakeBot()
    bot.script["edit_message_text"] = [make_error("bad", "message is not modified")]
    await ui.redraw(callback(bot), "x", None)
    assert len(bot.of("edit_message_text")) == 1
    await ui.redraw(callback(bot, with_message=False), "x", None)
    assert len(bot.of("edit_message_text")) == 1
    bot.script["edit_message_text"] = [make_error("net")]
    with pytest.raises(Exception, match="net"):
        await ui.redraw(callback(bot), "x", None)


async def test_redraw_rich():
    bot = FakeBot()
    rich = InputRichMessage(html="<b>x</b>")
    await ui.redraw_rich(callback(bot), rich, None)
    call = bot.of("edit_message_text")[0]
    assert call["rich_message"] == rich and "text" not in call
    bot.script["edit_message_text"] = [TelegramBadRequest(method=None, message="same")]
    await ui.redraw_rich(callback(bot), rich, None)
