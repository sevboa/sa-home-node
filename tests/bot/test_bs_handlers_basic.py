from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from drivers_w3d import drive_basic, load_shared

from sa_home_node import __version__
from sa_home_node.bot import handlers_basic
from sa_home_node.bot.commands import Command, Registry
from sa_home_node.bot.config import BotConfig
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.describe import ActionSpec, ServiceDescription
from sa_home_node.proto.wire import Address, ProtoError, ServiceUnavailableError

DATA = load_shared("handlers/basic.json")
NAME = "demo-node"
NS = SimpleNamespace(
    ActionSpec=ActionSpec,
    ServiceDescription=ServiceDescription,
    ProtoError=ProtoError,
    ServiceUnavailableError=ServiceUnavailableError,
    Address=Address,
)


def registry_of(case):
    return Registry(
        Command(
            name=spec["name"],
            description=spec["description"],
            universal=spec["universal"],
            right=spec["right"],
        )
        for spec in case.get("commands", DATA["commands"])
    )


def lines_of(case):
    return tuple(case.get("help_lines", ()))


KIT = SimpleNamespace(
    Subscription=Subscription,
    ns=NS,
    about=f"ℹ️ {NAME} v{__version__}",
    routers=[handlers_basic.router],
    setup=lambda case, sub: nullcontext(),
    build_help=lambda sub, skills, case: handlers_basic.build_help(
        sub, skills, registry_of(case), NAME, lines_of(case)
    ),
    data=lambda case, **kw: {
        **kw,
        "registry": registry_of(case),
        "bot_config": BotConfig(about_name=NAME),
        "help_lines": lines_of(case),
    },
)


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_basic_matches_prototype(case):
    assert await drive_basic(case, KIT) == case["expected"]
