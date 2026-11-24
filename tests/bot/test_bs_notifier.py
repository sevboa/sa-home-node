from __future__ import annotations

import pytest
from botfix import load, play
from fakebot import FakeBot

from sa_home_node.bot.notifier import Notifier

CASES = load("notifier/calls.json")["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
async def test_notifier_matches_prototype(case, monkeypatch):
    got = await play(case, Notifier, monkeypatch)
    assert got == {key: case[key] for key in got}


def test_bot_property():
    bot = FakeBot()
    assert Notifier(bot).bot is bot
