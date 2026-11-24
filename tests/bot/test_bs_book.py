from __future__ import annotations

from types import SimpleNamespace

from botfix import load
from fakebot import FakeBot, make_error, record_sleeps
from sa_home_node.bot.book import RETIRED_RIGHTS, SubscriptionBook, ValidationIssue
from sa_home_node.bot.subs import SOURCE_GUEST, Subscription

DATA = load("subs/book.json")


def row(s: Subscription) -> dict:
    return {
        "name": s.name,
        "chat_id": s.chat_id,
        "source": s.source,
        "event_types": sorted(s.event_types),
        "except_event_types": sorted(s.except_event_types),
        "allowed_commands": sorted(s.allowed_commands),
        "broken": s.broken,
        "invited_by_chat_id": s.invited_by_chat_id,
        "invited_at": s.invited_at,
        "invited_user": s.invited_user,
    }


def make(tags=None) -> SubscriptionBook:
    owners = [SimpleNamespace(**c) for c in DATA["owners"]]
    guests = [SimpleNamespace(**g) for g in DATA["guests"]]
    return SubscriptionBook.from_config(owners, guests, tags=tags or {})


def test_from_config_and_queries(caplog):
    book = make()
    assert [row(s) for s in book.all()] == DATA["after_load"]
    assert "tell_guests@llm" in RETIRED_RIGHTS
    assert "1001" in caplog.text
    assert {e: [s.name for s in book.accepting(e)] for e in DATA["accepting"]} == DATA["accepting"]
    assert {c: (s.name if (s := book.for_chat(int(c))) else None) for c in DATA["for_chat"]} == DATA[
        "for_chat"
    ]
    assert [s.name for s in book.guests()] == DATA["guests_names"]


def test_add_replace_remove():
    book = make()
    new = DATA["replacement"]
    book.add(
        Subscription(
            name=new["name"],
            chat_id=new["chat_id"],
            event_types=frozenset(new["event_types"]),
            allowed_commands=frozenset(new["allowed_commands"]),
            source=SOURCE_GUEST,
        )
    )
    assert [s.name for s in book.all()] == DATA["after_replace"]
    book.add(Subscription(name="guest-c", chat_id=3001, event_types=frozenset({"*"}), source=SOURCE_GUEST))
    assert [s.name for s in book.all()] == DATA["after_add"]
    assert {c: book.remove(int(c)) for c in DATA["removed"]} == DATA["removed"]
    assert [s.name for s in book.all()] == DATA["after_remove"]


def test_tags_applied_on_create_and_add():
    book = make(tags={1001: frozenset({"t"}), 2001: frozenset({"u"})})
    assert book.for_chat(1001).tags == frozenset({"t"})
    assert book.for_chat(-100500).tags == frozenset()
    book.add(Subscription(name="again", chat_id=1001))
    assert book.for_chat(1001).tags == frozenset({"t"})
    assert SubscriptionBook([Subscription(name="x", chat_id=5)], {5: frozenset({"z"})}).for_chat(
        5
    ).tags == frozenset({"z"})


class ChatBot(FakeBot):
    def __init__(self, scripts: dict[str, list[str]]) -> None:
        super().__init__()
        self.scripts = {k: list(v) for k, v in scripts.items()}

    async def get_chat(self, chat_id, **kwargs):
        token = self.scripts[str(chat_id)].pop(0) if self.scripts[str(chat_id)] else "ok"
        self.script["get_chat"] = [None if token == "ok" else make_error(token, str(chat_id))]
        return await super().get_chat(chat_id, **kwargs)


async def test_validate_on_startup(monkeypatch):
    expected = DATA["validate"]
    sleeps = record_sleeps(monkeypatch)
    book = make()
    bot = ChatBot(expected["scripts"])
    issues = await book.validate_on_startup(bot)
    assert [ValidationIssue(i["chat_id"], i["name"], i["reason"]) for i in expected["issues"]] == issues
    assert [[c["chat_id"], c["request_timeout"]] for c in bot.of("get_chat")] == expected["calls"]
    assert sleeps == expected["sleeps"]
    assert {s.name: s.broken for s in book.all()} == expected["broken"]
