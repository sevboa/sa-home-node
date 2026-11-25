from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
from aiogram.types import Update
from botfix import load
from drivers_w2a import message_dict, user_dict
from drivers_w3d import W3Bot
from fakelink import FakeLink
from stub_plugin import HELLO_RIGHT, stub_plugin

from sa_home_node.bot.alerts import AlertDispatcher, build_event_handler
from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.commands import Registry
from sa_home_node.bot.config import BotConfig
from sa_home_node.bot.context import BotContext
from sa_home_node.bot.db import AppState, open_bot_db
from sa_home_node.bot.gate import Gatekeeper
from sa_home_node.bot.guestfile import GuestStore
from sa_home_node.bot.nodeevents import build_node_event_handler
from sa_home_node.bot.notifier import Notifier
from sa_home_node.bot.rights import RightsCatalog
from sa_home_node.bot.setup import build_dispatcher
from sa_home_node.bot.store_events import EventStore
from sa_home_node.bot.store_invites import InviteStore
from sa_home_node.bot.store_outbox import OutboxStore
from sa_home_node.proto.wire import Envelope

SCENARIO = load("wiring/scenario.json")
ENTRY_TIME = re.compile(r"вход \d{4}-\d\d-\d\d \d\d:\d\d")
RECORDED = ("send_message", "edit_message_text", "answer_callback_query")


class WiringBot(W3Bot):
    id = 1


class CountingGuestStore(GuestStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.saves = 0

    def save(self, guests: Any) -> bool:
        self.saves += 1
        return super().save(guests)


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.bot = WiringBot()
        self.admitted: list[tuple[int, int]] = []
        self.code = ""
        self.guests_path = tmp_path / "guests.toml"

    async def open(self) -> None:
        plugin = stub_plugin(self.admitted)
        spec = plugin.bot
        config = BotConfig.model_validate(SCENARIO["config"])
        self.db = await open_bot_db(self.tmp_path / "bot.sqlite")
        book = SubscriptionBook.from_config(config.subscriptions, config.guest_subscriptions)
        notifier = Notifier(self.bot, outbox=OutboxStore(self.db))
        self.guest_store = CountingGuestStore(self.guests_path)
        self.gate = Gatekeeper(config.invites, InviteStore(self.db), book, self.guest_store, notifier)
        link = FakeLink({(None, "actions"): ()})
        events = EventStore(self.db)
        self.ctx = BotContext(
            config=config,
            bot=self.bot,
            bot_username="fakebot",
            book=book,
            notifier=notifier,
            events=events,
            app_state=AppState(self.db),
            db=self.db,
            node_link=link,
            monitor_link=link,
            apps_link=link,
            registry=Registry(spec.commands),
            catalog=RightsCatalog(spec.guest_rights),
            guest_store=self.guest_store,
            gate=self.gate,
            on_admitted=spec.on_admitted,
        )
        self.dp = build_dispatcher(self.ctx, [plugin])
        self.monitor_events = build_event_handler(AlertDispatcher(notifier, book, events))
        self.node_events = build_node_event_handler(self.ctx, spec.event_routes, config.about_name)

    def update(self, step: dict[str, Any]) -> Update:
        person = user_dict({"id": step["chat"], "first_name": "Тест"})
        spec = {"chat": {"id": step["chat"]}, "user": {"id": step["chat"], "first_name": "Тест"}}
        if step["do"] == "message":
            body = {"message": message_dict({**spec, "text": step["text"].replace("{code}", self.code)})}
        else:
            query = {
                "id": "cb",
                "from": person,
                "chat_instance": "ci",
                "data": step["data"],
                "message": message_dict(spec),
            }
            body = {"callback_query": query}
        return Update.model_validate({"update_id": 1, **body}, context={"bot": self.bot})

    async def play(self, step: dict[str, Any]) -> None:
        kind = step["do"]
        if kind == "issue":
            self.code, _ = await self.gate.issue(step["chat"], None)
        elif kind == "monitor":
            await self.monitor_events(
                Envelope("event", "m", payload={"event": step["event"], "data": step["data"]})
            )
        elif kind == "node":
            await self.node_events(
                Envelope("event", "n", payload={"event": step["event"], "data": step["data"]})
            )
        else:
            await self.dp.feed_update(self.bot, self.update(step))

    def observed(self) -> dict[str, Any]:
        sent = [
            [name, kwargs.get("chat_id"), ENTRY_TIME.sub("вход <время>", kwargs.get("text") or "")]
            for name, kwargs in self.bot.calls
            if name in RECORDED
        ]
        self.bot.calls.clear()
        return {"sent": sent, "admitted": [list(item) for item in self.admitted]}


pytestmark = pytest.mark.usefixtures("detached_routers")


@pytest.fixture
async def rig(tmp_path):
    rig = Rig(tmp_path)
    await rig.open()
    yield rig
    await rig.db.close()


def guest_file_rights(path: Path) -> list[str]:
    if not path.is_file():
        return []
    entries = tomllib.loads(path.read_text(encoding="utf-8")).get("guest_subscriptions", [])
    return sorted(right for entry in entries for right in entry["allowed_commands"])


async def test_scenario(rig):
    for step in SCENARIO["steps"]:
        saves = rig.guest_store.saves
        await rig.play(step)
        got = rig.observed()
        got["saves"] = rig.guest_store.saves - saves
        got["file_rights"] = guest_file_rights(rig.guests_path)
        assert got == step["expect"], step["name"]
    assert HELLO_RIGHT in rig.ctx.catalog.grantable()
