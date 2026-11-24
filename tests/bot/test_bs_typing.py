from __future__ import annotations

import pytest
from botfix import drive_typing, load

from sa_home_node.bot import typing
from sa_home_node.bot.typing import TypingIndicator, typing_action

DATA = load("typing/ticks.json")


def test_constants():
    assert (typing.TYPING_KEEPALIVE_INTERVAL_S, typing.TYPING_MAX_TICKS) == (
        DATA["interval"],
        DATA["max_ticks"],
    )


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_typing_matches_prototype(case):
    got = await drive_typing(case, TypingIndicator, typing_action)
    assert got == {key: case[key] for key in got}
