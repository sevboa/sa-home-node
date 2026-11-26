from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.types import Message
from botfix import load
from drivers_w2a import drive_middleware

from sa_home_node.bot import middlewares
from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.commands import Command, Registry
from sa_home_node.bot.middlewares import (
    AuthorizationMiddleware,
    CallbackAuthorizationMiddleware,
    SilenceGate,
    extract_command,
    invite_candidate,
)
from sa_home_node.bot.subs import Subscription

DATA = load("subs/authz.json")
TABLE = load("commands/table.json")
PLUGIN = Command(**TABLE["plugin"]["command"], callback_codes=tuple(TABLE["plugin"]["codes"]))
REGISTRY = Registry((PLUGIN,))
HELPERS = DATA["helpers"]


def make_sub(name, chat_id, rights, **extra):
    return Subscription(name=name, chat_id=chat_id, allowed_commands=frozenset(rights), **extra)


def vpn_guard(subscription, service, action_id):
    return (
        service == "vpn"
        and action_id == "report"
        and (subscription.allows_action("report", "vpn") or subscription.allows_action("usage", "vpn"))
    )


KIT = SimpleNamespace(
    book=SubscriptionBook,
    sub=make_sub,
    silence=SilenceGate,
    auth=lambda book: AuthorizationMiddleware(book, REGISTRY),
    callback=lambda book: CallbackAuthorizationMiddleware(book, REGISTRY, guards=(vpn_guard,)),
)


def message(text):
    body = {"message_id": 1, "date": 1700000000, "chat": {"id": 1, "type": "private"}}
    return Message.model_validate({**body, "text": text} if text is not None else body)


def test_denied_texts():
    assert middlewares.DENIED_TEXT == DATA["denied_text"]
    assert middlewares.DENIED_ALERT_TEXT == DATA["denied_alert_text"]


@pytest.mark.parametrize("pair", HELPERS["candidate"], ids=lambda p: repr(p[0]))
def test_invite_candidate(pair):
    assert invite_candidate(None if pair[0] is None else message(pair[0])) == pair[1]


def test_invite_candidate_without_text():
    assert invite_candidate(message(None)) == HELPERS["empty_text"]


@pytest.mark.parametrize("pair", HELPERS["extract"], ids=lambda p: repr(p[0]))
def test_extract_command(pair):
    assert extract_command(pair[0]) == pair[1]


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_middleware_matches_prototype(case):
    got = await drive_middleware(case, DATA, KIT)
    assert got == {key: case[key] for key in got}
