from __future__ import annotations

import pytest
from botfix import load, play

from sa_home_node.bot.attach import AttachMixin

CASES = load("notifier/attach.json")["cases"]


class Host(AttachMixin):
    def __init__(self, bot):
        self.bot = bot


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
async def test_attachments_match_prototype(case, monkeypatch):
    got = await play(case, lambda bot, outbox: Host(bot), monkeypatch)
    assert got == {key: case[key] for key in got}
