from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from botfix import load
from drivers_w2a import drive_gate, drive_limiter

from sa_home_node.bot import gate
from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.config import InvitesConfig
from sa_home_node.bot.db import open_bot_db
from sa_home_node.bot.gate import AttemptLimiter, Gatekeeper, format_code, generate_code, normalize
from sa_home_node.bot.guestfile import GuestStore
from sa_home_node.bot.store_invites import InviteStore
from sa_home_node.bot.subs import Subscription

DATA = load("gate/cases.json")


async def open_invites(path):
    db = await open_bot_db(path, ())
    return InviteStore(db), db.close


def make_sub(name, chat_id, rights, **extra):
    return Subscription(name=name, chat_id=chat_id, allowed_commands=frozenset(rights), **extra)


KIT = SimpleNamespace(
    open_invites=open_invites,
    book=SubscriptionBook,
    sub=make_sub,
    guest_store=GuestStore,
    Gatekeeper=Gatekeeper,
    cfg=lambda spec: InvitesConfig(**spec),
    alphabet=gate.ALPHABET,
)


def test_constants():
    assert gate.ALPHABET == DATA["alphabet"]
    assert gate.CODE_LENGTH == DATA["code_length"]
    assert gate.GROUP == DATA["group"]
    assert AttemptLimiter.WINDOW_S == DATA["window_s"]


def test_generate_code():
    codes = {generate_code() for _ in range(30)}
    assert len(codes) > 1
    assert all(re.fullmatch(f"[{DATA['alphabet']}]{{8}}", code) for code in codes)


@pytest.mark.parametrize("pair", DATA["normalize"], ids=lambda p: repr(p[0]))
def test_normalize(pair):
    assert normalize(pair[0]) == pair[1]


@pytest.mark.parametrize("pair", DATA["format"], ids=lambda p: repr(p[0]))
def test_format_code(pair):
    assert format_code(pair[0]) == pair[1]


@pytest.mark.parametrize("case", DATA["limiter"], ids=lambda c: c["name"])
async def test_limiter(case):
    assert await drive_limiter(case, AttemptLimiter(case["limit"])) == case["out"]


@pytest.mark.parametrize("case", DATA["gatekeeper"], ids=lambda c: c["name"])
async def test_gatekeeper_matches_prototype(case, tmp_path):
    got = await drive_gate(case, KIT, tmp_path)
    assert got == {key: case[key] for key in got}
