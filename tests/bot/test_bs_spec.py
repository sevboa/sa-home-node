from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from sa_home_node.bot.spec import BotSpec, EventRoute, RouterSpec

from sa_home_node.plugins import Plugin

TUPLES = (
    "commands",
    "routers",
    "middlewares",
    "guest_rights",
    "event_routes",
    "schema",
    "callback_guards",
    "help_lines",
)
NONES = ("subscription_tags", "egress_candidates", "on_admitted", "on_start", "on_stop", "wake_guard")


def test_empty_spec():
    spec = BotSpec()
    assert all(getattr(spec, name) == () for name in TUPLES)
    assert all(getattr(spec, name) is None for name in NONES)


def test_fields_are_kept_and_frozen():
    hook = object()
    spec = BotSpec(schema=("CREATE TABLE IF NOT EXISTS t(a)",), help_lines=("a", "b"), on_start=hook)
    assert spec.schema == ("CREATE TABLE IF NOT EXISTS t(a)",)
    assert spec.help_lines == ("a", "b")
    assert spec.on_start is hook
    with pytest.raises(FrozenInstanceError):
        spec.help_lines = ()


def test_router_and_event_route():
    build = object()
    router = RouterSpec(order=3, build=build)
    route = EventRoute(names=frozenset({"a", "b"}), handle=build)
    assert (router.order, router.build) == (3, build)
    assert route.names == frozenset({"a", "b"})
    assert BotSpec(routers=(router,), event_routes=(route,)).routers[0] is router


def test_plugin_carries_bot_spec():
    spec = BotSpec(help_lines=("x",))
    assert Plugin(name="p").bot is None
    assert Plugin(name="p", bot=spec).bot is spec
