from __future__ import annotations

import pytest
from botfix import load

from sa_home_node.bot import commands
from sa_home_node.bot.commands import Command, Registry
from sa_home_node.bot.subs import Subscription

T = load("commands/table.json")
PLUGIN = Command(**T["plugin"]["command"], callback_codes=tuple(T["plugin"]["codes"]))
SAMPLE = [Command(**c) for c in T["menu_sample"]["commands"]]


def sub_of(allowed):
    return None if allowed is None else Subscription(name="s", chat_id=1, allowed_commands=frozenset(allowed))


@pytest.mark.parametrize("case", T["core"], ids=lambda c: c["name"])
def test_core_commands(case):
    registry = Registry(())
    command = registry.get(case["name"])
    assert (command.name, command.description, command.universal, command.menu, command.right) == (
        case["name"],
        case["description"],
        case["universal"],
        case["menu"],
        case["right"],
    )
    assert registry.required_right(case["name"]) == case["required_right"]
    assert registry.is_universal(case["name"]) is case["is_universal"]
    assert registry.is_control(case["name"]) is case["is_control"]


def test_unknown_command():
    registry = Registry(())
    unknown = T["unknown"]
    assert registry.get(unknown["name"]) is None
    assert registry.required_right(unknown["name"]) == unknown["required_right"]
    assert registry.is_universal(unknown["name"]) is unknown["is_universal"]
    assert registry.is_control(unknown["name"]) is unknown["is_control"]


def test_plugin_command_joins_registry():
    registry = Registry((PLUGIN,))
    assert registry.get("torrents") is PLUGIN
    assert registry.required_right("torrents") == "list@torrents"
    assert registry.get("ping").name == "ping"


@pytest.mark.parametrize("name", sorted(T["constants"]))
def test_constants(name):
    assert getattr(commands, name) == T["constants"][name]


@pytest.mark.parametrize("case", T["callback_for"], ids=lambda c: repr(c["data"]))
def test_command_for_callback(case):
    command = Registry((PLUGIN,)).command_for_callback(case["data"])
    assert (command.name if command else None) == case["command"]


def test_plugin_codes_unknown_without_plugin():
    for code in T["plugin"]["codes"]:
        assert Registry(()).command_for_callback(f"st:{code}") is None
        assert Registry((PLUGIN,)).command_for_callback(f"st:{code}").name == "torrents"


def test_code_map_is_covered():
    registry = Registry(())
    for code, name in T["code_map"].items():
        assert registry.command_for_callback(f"st:{code}").name == name
    assert {k: v for k, v in T["status_actions"].items()} == {
        k: T["code_map"][k] for k in T["status_actions"]
    }


@pytest.mark.parametrize("key", sorted(T["menu_sample"]["subs"]))
def test_menu_control_commands(key):
    registry = Registry(tuple(SAMPLE))
    sub = sub_of(T["menu_sample"]["subs"][key])
    assert [c.name for c in registry.menu_control_commands_for(sub)] == T["menu_sample"]["out"][key]


@pytest.mark.parametrize(
    "case", T["can_run_action"], ids=lambda c: f"{c['sub']}-{c['action']}-{c['service']}"
)
def test_can_run_action(case):
    assert (
        Registry(()).can_run_action(sub_of(case["allowed"]), case["action"], case["service"]) is case["out"]
    )


@pytest.mark.parametrize("case", T["formats"]["panel"], ids=lambda c: repr(c["args"]))
def test_panel_callback(case):
    assert commands.panel_callback(*case["args"]) == case["out"]


@pytest.mark.parametrize("case", T["formats"]["action"], ids=lambda c: repr(c["args"]))
def test_action_callback(case):
    assert commands.action_callback(*case["args"]) == case["out"]


@pytest.mark.parametrize("case", T["formats"]["wake"], ids=lambda c: repr(c["arg"]))
def test_wake_callback(case):
    assert commands.wake_callback(case["arg"]) == case["out"]


@pytest.mark.parametrize("case", T["formats"]["parse_wake"], ids=lambda c: repr(c["data"]))
def test_parse_wake_callback(case):
    assert commands.parse_wake_callback(case["data"]) == case["out"]


@pytest.mark.parametrize("case", T["formats"]["parse_action"], ids=lambda c: repr(c["data"]))
def test_parse_action_callback(case):
    parsed = commands.parse_action_callback(case["data"])
    assert (list(parsed) if parsed is not None else None) == case["out"]


def test_action_roundtrip():
    data = commands.action_callback("restart", "v", "n1", "svc")
    assert commands.parse_action_callback(data) == ("svc", "restart", "v", "n1")
