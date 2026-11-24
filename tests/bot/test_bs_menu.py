from __future__ import annotations

from types import SimpleNamespace

import pytest
from botfix import load
from drivers import drive_menu

from sa_home_node.bot import menu
from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.commands import Command, Registry
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.describe import ActionParam, ActionSpec

DATA = load("menu/commands.json")
REGISTRY = Registry([Command(**spec) for spec in DATA["commands"]])


def make_sub(name, spec):
    return Subscription(
        name=name,
        chat_id=spec["chat_id"],
        allowed_commands=frozenset(spec["allowed_commands"]),
        broken=spec["broken"],
    )


def make_action(spec):
    params = (ActionParam(name="p"),) if spec["params"] else ()
    return ActionSpec(id=spec["id"], title=spec["title"], params=params)


async def set_all(bot, book, actions):
    await menu.set_bot_commands(bot, book, REGISTRY, skill_actions=actions)


KIT = SimpleNamespace(
    sub=make_sub,
    action=make_action,
    book=SubscriptionBook,
    set_all=set_all,
    build=lambda sub, actions: menu.build_menu_commands(sub, REGISTRY, skill_actions=actions),
    refresh=lambda bot, chat, sub, actions: menu.refresh_chat_menu(
        bot, chat, sub, REGISTRY, skill_actions=actions
    ),
)


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_menu_matches_prototype(case):
    got = await drive_menu(case, DATA, KIT)
    assert got == {key: case[key] for key in got}
