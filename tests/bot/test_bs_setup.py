from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiogram import Router
from aiogram.dispatcher.event.bases import UNHANDLED
from botfix import FIX
from drivers_w3d import State, W3Bot, build_callback
from fakelink import FakeLink

from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.commands import Registry
from sa_home_node.bot.config import BotConfig
from sa_home_node.bot.handlers_wake import NOT_CONFIGURED_TEXT
from sa_home_node.bot.setup import build_bot, build_dispatcher
from sa_home_node.bot.spec import BotSpec, RouterSpec
from sa_home_node.bot.subs import Subscription
from sa_home_node.plugins import Plugin

OWNER = Subscription(name="owner", chat_id=12345, allowed_commands=frozenset({"*"}))

pytestmark = pytest.mark.usefixtures("detached_routers")


def sample_plugin() -> Plugin:
    specs = tuple(
        RouterSpec(order=order, build=lambda ctx, order=order: Router(name=f"plugin{order}"))
        for order in (950, 50, 150, 100, 899, 900)
    )
    return Plugin(name="sample", bot=BotSpec(routers=specs))


def make_ctx(link: FakeLink, state: State) -> SimpleNamespace:
    return SimpleNamespace(
        bot=None,
        config=BotConfig(),
        bot_username="fakebot",
        book=SubscriptionBook([OWNER]),
        notifier=None,
        gate=None,
        guest_store=None,
        catalog=None,
        registry=Registry(),
        guest_usage=None,
        on_admitted=None,
        wake_guard=None,
        help_lines=(),
        node_link=link,
        monitor_link=link,
        apps_link=link,
        app_state=state,
    )


def test_router_order_matches_plan():
    ctx = make_ctx(FakeLink(), State())
    dp = build_dispatcher(ctx, [sample_plugin()])
    expected = json.loads((FIX / "setup" / "order.json").read_text(encoding="utf-8"))
    assert [router.name for router in dp.sub_routers] == expected


async def test_wake_update_does_not_reach_status():
    link = FakeLink()
    state = State()
    dp = build_dispatcher(make_ctx(link, state), [])
    bot = W3Bot()
    callback = build_callback({"data": "st:wake"}, bot)
    result = await dp.propagate_event("callback_query", callback, **dp.workflow_data)
    assert result is not UNHANDLED
    assert [call[1]["text"] for call in bot.dump() if call[0] == "send_message"] == [NOT_CONFIGURED_TEXT]
    assert link.calls == []


def test_build_bot_defaults_to_html():
    bot = build_bot("1:abc")
    assert bot.default.parse_mode == "HTML"
    assert bot.session.proxy is None
    assert build_bot("1:abc", "socks5://127.0.0.1:1").session.proxy == "socks5://127.0.0.1:1"
