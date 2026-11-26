from __future__ import annotations

from sa_home_node.bot.config import BotServiceConfig
from sa_home_node.bot.plugin import PLUGIN
from sa_home_node.plugins import load_plugins


def test_plugin_is_loaded_always():
    assert PLUGIN in load_plugins([])


async def test_service_is_singleton_instanced_without_endpoint(monkeypatch):
    seen = []

    async def fake_run_bot(ctx):
        seen.append(ctx)
        return True

    monkeypatch.setattr("sa_home_node.bot.run.run_bot", fake_run_bot)
    (spec,) = PLUGIN.services
    assert spec.name == "telegram-bot"
    assert await spec.run("ctx") is True
    assert seen == ["ctx"]
    assert spec.process and spec.singleton and spec.instanced
    assert spec.satellites == (".guests",)
    assert spec.endpoint is None
    assert spec.handler is None


def test_service_config_is_bot_service_config():
    assert PLUGIN.config_model is BotServiceConfig
    assert PLUGIN.fixups is None
