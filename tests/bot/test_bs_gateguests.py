from __future__ import annotations

from types import SimpleNamespace

import pytest
from botfix import load
from drivers_w2a import drive_guests

from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.gateguests import add_guest_right, remove_guest_right, revoke_guest, set_guest_rights
from sa_home_node.bot.subs import Subscription

DATA = load("gate/guests.json")


def make_sub(name, chat_id, rights, **extra):
    return Subscription(name=name, chat_id=chat_id, allowed_commands=frozenset(rights), **extra)


KIT = SimpleNamespace(
    book=SubscriptionBook,
    sub=make_sub,
    revoke=revoke_guest,
    set_rights=set_guest_rights,
    add_right=add_guest_right,
    remove_right=remove_guest_right,
)


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
def test_guest_operations_match_prototype(case):
    got = drive_guests({**case, "subs": DATA["subs"]}, KIT)
    assert got == {key: case[key] for key in got}
