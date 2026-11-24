from __future__ import annotations

import logging
import tomllib

from botfix import FIX, load
from sa_home_node.bot.guestfile import HEADER, GuestStore, render
from sa_home_node.bot.subs import SOURCE_GUEST, Subscription

GUESTS = load("guests/input.json")


def subs() -> list[Subscription]:
    return [
        Subscription(
            name=g["name"],
            chat_id=g["chat_id"],
            event_types=frozenset(g["event_types"]),
            allowed_commands=frozenset(g["allowed_commands"]),
            source=SOURCE_GUEST,
            invited_by_chat_id=g["invited_by_chat_id"],
            invited_at=g["invited_at"],
            invited_user=g["invited_user"],
        )
        for g in GUESTS
    ]


def test_render_bytes_equal_to_prototype():
    data = render(subs())
    assert data == (FIX / "guests" / "package.toml").read_bytes()
    parsed = tomllib.loads(data.decode())["guest_subscriptions"]
    assert [g["chat_id"] for g in parsed] == sorted(g["chat_id"] for g in GUESTS)
    assert {g["name"] for g in parsed} == {g["name"] for g in GUESTS}


def test_render_empty_is_header():
    assert render([]) == (FIX / "guests" / "empty.toml").read_bytes() == HEADER.encode("utf-8")


def test_store_saves_and_logs(tmp_path, caplog):
    path = tmp_path / "guests.toml"
    store = GuestStore(path)
    with caplog.at_level(logging.INFO):
        assert store.available and store.save(subs()) is True
    assert path.read_bytes() == render(subs())
    assert any(r.levelno == logging.INFO for r in caplog.records)


def test_store_without_path():
    store = GuestStore(None)
    assert store.available is False
    assert store.save(subs()) is False
