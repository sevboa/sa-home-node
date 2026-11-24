from __future__ import annotations

import logging

import pytest
from botfix import FIX, load
from pydantic import ValidationError
from sa_home_node.bot.config import BotConfig, GuestSubscriptionConfig, SubscriptionConfig, load_bot_config

CASES = ("full", "shadow", "empty")


@pytest.mark.parametrize("case", CASES)
def test_load_matches_prototype(case, caplog):
    directory = FIX / "config" / case
    expected = load(f"config/{case}/expected.json")
    guests = directory / "guests.toml"
    with caplog.at_level(logging.WARNING):
        config = load_bot_config(
            directory / "config.toml", directory / "package.toml", guests if expected["has_guests"] else None
        )
    assert config.telegram.model_dump() == expected["telegram"]
    assert config.invites.model_dump() == expected["invites"]
    assert [s.model_dump() for s in config.subscriptions] == expected["subscriptions"]
    assert [s.model_dump() for s in config.guest_subscriptions] == expected["guest_subscriptions"]
    for section in ("telegram", "invites", "subscriptions"):
        assert (f"[{section}]" in caplog.text) == (section in expected["shadowed"])


def test_missing_guest_file_is_ignored():
    directory = FIX / "config" / "empty"
    config = load_bot_config(directory / "config.toml", directory / "package.toml", directory / "absent.toml")
    assert config.guest_subscriptions == []


def test_defaults():
    config = BotConfig()
    assert config.about_name == "sa-home-node"
    assert config.invites.grant_commands == []
    assert (config.invites.enabled, config.invites.ttl_s, config.invites.max_attempts_per_hour) == (
        True,
        3600.0,
        5,
    )
    assert (config.telegram.proxy_mode, config.telegram.prefer_direct) == ("fixed", True)
    assert config.subscriptions == [] and config.guest_subscriptions == []
    assert SubscriptionConfig(name="a", chat_id=1).event_types == ["*"]
    assert GuestSubscriptionConfig(name="a", chat_id=1).invited_by_chat_id == 0


def test_bad_proxy_mode_rejected():
    with pytest.raises(ValidationError):
        BotConfig(telegram={"proxy_mode": "other"})
