from __future__ import annotations

from sa_home_node.bot.config import BotServiceConfig
from sa_home_node.bot.plugin import PLUGIN
from sa_home_node.bot.run import run_bot
from sa_home_node.plugins import load_plugins


def test_plugin_is_loaded_always():
    assert PLUGIN in load_plugins([])


def test_service_is_singleton_instanced_without_endpoint():
    (spec,) = PLUGIN.services
    assert spec.name == "telegram-bot"
    assert spec.run is run_bot
    assert spec.process and spec.singleton and spec.instanced
    assert spec.satellites == (".guests",)
    assert spec.endpoint is None
    assert spec.handler is None


def test_service_config_is_bot_service_config():
    assert PLUGIN.config_model is BotServiceConfig
    assert PLUGIN.fixups is None
