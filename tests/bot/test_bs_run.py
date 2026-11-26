from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram import Dispatcher
from aiogram.exceptions import TelegramConflictError
from aiogram.methods import GetMe
from fakebot import FakeBot, make_error
from fakelink import FakeLink

from sa_home_node.bot import run
from sa_home_node.bot.config import BotServiceConfig
from sa_home_node.bot.lifecycle import render_shutdown, render_startup
from sa_home_node.bot.spec import BotSpec
from sa_home_node.plugins import Plugin, ServiceContext

TOKEN_TOML = (
    '[telegram]\ntoken = "1:abc"\n[[subscriptions]]\nname = "owner"\nchat_id = 7\nallowed_commands = ["*"]\n'
)


class RunBot(FakeBot):
    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self.order = order

        async def close() -> None:
            order.append("bot:close")

        self.session = SimpleNamespace(middleware=lambda mw: order.append("bot:middleware"), close=close)


class RunLink(FakeLink):
    def __init__(self, order: list[str], endpoint: Any, **kwargs: Any) -> None:
        super().__init__({(None, "actions"): ()})
        self.order = order
        self.name = kwargs["display_name"]

    async def start(self) -> None:
        self.order.append(f"link:{self.name}:start")

    async def stop(self) -> None:
        self.order.append(f"link:{self.name}:stop")


class Rig:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.order: list[str] = []
        self.release = asyncio.Event()
        self.polling_error: BaseException | None = None
        self.bot = RunBot(self.order)
        self.plugin = Plugin(
            name="sample",
            bot=BotSpec(
                schema=("CREATE TABLE IF NOT EXISTS plug(a)",), on_start=self.on_start, on_stop=self.on_stop
            ),
        )
        self.fail_on_start = False
        (tmp_path / "node.toml").write_text(TOKEN_TOML, encoding="utf-8")
        (tmp_path / "package.toml").write_text("", encoding="utf-8")
        self.ctx = ServiceContext(
            node_id="n",
            data_dir=tmp_path,
            config=BotServiceConfig(
                config_path=tmp_path / "node.toml",
                db_path=tmp_path / "bot.sqlite",
                egress_state_path=tmp_path / "e.json",
            ),
            content=None,
            package_path=tmp_path / "package.toml",
            plugins=(self.plugin,),
            node_socket="node.sock",
            swarm_token="t",
        )
        order = self.order
        real_open = run.open_bot_db

        async def open_db(path: Any, schema: Any = ()) -> Any:
            db = await real_open(path, schema)
            real_close = db.close

            async def close() -> None:
                order.append("db:close")
                await real_close()

            db.close = close
            return db

        async def polling(dp: Dispatcher, *bots: Any, **kwargs: Any) -> None:
            order.append("polling:begin")
            await self.release.wait()
            await dp.emit_startup()
            order.append("polling:started")
            if self.polling_error is not None:
                raise self.polling_error
            await asyncio.Event().wait()

        monkeypatch.setattr(run, "open_bot_db", open_db)
        monkeypatch.setattr(run, "build_bot", lambda token, proxy="": self.bot)
        monkeypatch.setattr(run, "ServiceLink", lambda endpoint, **kw: RunLink(order, endpoint, **kw))
        monkeypatch.setattr(run, "build_dispatcher", lambda ctx, plugins: Dispatcher())
        monkeypatch.setattr(Dispatcher, "start_polling", polling)

    async def on_start(self, ctx: Any) -> None:
        rows = await ctx.db.fetch_all("SELECT name FROM sqlite_master WHERE name = 'plug'")
        self.order.append(f"plugin:start:{len(rows)}")
        if self.fail_on_start:
            raise RuntimeError("boom")

    async def on_stop(self, ctx: Any) -> None:
        self.order.append("plugin:stop")

    def texts(self) -> list[str]:
        return [call["text"] for call in self.bot.of("send_message")]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


async def settle(rig: Rig, marker: str) -> None:
    for _ in range(200):
        if marker in rig.order:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(marker)


async def test_start_and_stop_order(rig, caplog):
    with caplog.at_level(logging.INFO):
        task = asyncio.create_task(run.run_bot(rig.ctx))
        await settle(rig, "polling:begin")
        assert "Бот запущен" not in caplog.text
        rig.release.set()
        await settle(rig, "polling:started")
        for _ in range(50):
            if len(rig.texts()) == 1:
                break
            await asyncio.sleep(0.01)
        assert "Бот запущен" in caplog.text
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert rig.order == [
        "bot:middleware",
        "link:монитор:start",
        "link:нода:start",
        "link:приложения:start",
        "plugin:start:1",
        "polling:begin",
        "polling:started",
        "plugin:stop",
        "link:приложения:stop",
        "link:нода:stop",
        "link:монитор:stop",
        "bot:close",
        "db:close",
    ]
    assert rig.texts() == [render_startup(True), render_shutdown()]


async def test_conflict_yields_service_without_goodbye(rig):
    rig.polling_error = TelegramConflictError(GetMe(), "conflict")
    rig.release.set()
    assert await run.run_bot(rig.ctx) is True
    assert rig.texts() == [render_startup(True)]
    assert rig.order[-2:] == ["bot:close", "db:close"]


@pytest.mark.parametrize("stage", ["get_me", "plugin_start"])
async def test_startup_error_closes_opened(rig, stage):
    if stage == "get_me":
        rig.bot.script["get_me"] = [make_error("runtime")]
    else:
        rig.fail_on_start = True
    with pytest.raises(RuntimeError):
        await run.run_bot(rig.ctx)
    assert rig.order[-2:] == ["bot:close", "db:close"]
    assert "polling:begin" not in rig.order
    assert rig.texts() == []
    if stage == "get_me":
        assert not any(item.startswith("link:") for item in rig.order)
    else:
        assert rig.order.index("link:приложения:stop") < rig.order.index("bot:close")


async def test_auto_egress_starts_after_outbox_and_stops_before(rig, monkeypatch):
    order = rig.order

    class Egress:
        def __init__(self, session: Any, config: Any, fetch: Any, probe: Any, **kwargs: Any) -> None:
            order.append("egress:new")

        def start(self) -> None:
            order.append("egress:start")

        async def stop(self) -> None:
            order.append("egress:stop")

    async def pick(bot: Any, routes: list[str]) -> None:
        order.append("egress:pick")

    monkeypatch.setattr(run, "EgressManager", Egress)
    monkeypatch.setattr(run, "pick_startup_route", pick)
    (rig.ctx.config.config_path).write_text(
        TOKEN_TOML.replace('token = "1:abc"', 'token = "1:abc"\nproxy_mode = "auto"')
    )
    rig.release.set()
    task = asyncio.create_task(run.run_bot(rig.ctx))
    await settle(rig, "polling:started")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert order[0] == "egress:pick"
    assert order.index("egress:start") > order.index("link:приложения:start")
    assert order.index("egress:stop") < order.index("link:приложения:stop")
    assert order.index("egress:stop") > order.index("plugin:stop")
