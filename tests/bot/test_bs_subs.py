from __future__ import annotations

import pytest
from botfix import load
from sa_home_node.bot.subs import SOURCE_CONFIG, SOURCE_GUEST, WILDCARD, Subscription

TABLE = load("subs/decisions.json")


def build(d: dict) -> Subscription:
    return Subscription(
        name=d["name"],
        chat_id=d["chat_id"],
        event_types=frozenset(d["event_types"]),
        except_event_types=frozenset(d["except_event_types"]),
        allowed_commands=frozenset(d["allowed_commands"]),
        broken=d["broken"],
        source=d["source"],
    )


@pytest.mark.parametrize("row", TABLE["rows"], ids=lambda r: r["def"]["name"])
def test_decisions(row):
    sub = build(row["def"])
    assert (sub.is_guest, sub.is_owner) == (row["is_guest"], row["is_owner"])
    assert {c: sub.allows_command(c) for c in TABLE["commands"]} == row["commands"]
    assert {f"{a}@{s}": sub.allows_action(a, s) for a, s in TABLE["actions"]} == row["actions"]
    assert {e: sub.accepts_event(e) for e in TABLE["events"]} == row["events"]
    assert sub.with_broken(True).is_owner is row["with_broken_owner"]
    assert sub.with_allowed_commands(frozenset({WILDCARD})).is_owner is row["with_all_owner"]


def test_constants_and_defaults():
    assert (WILDCARD, SOURCE_CONFIG, SOURCE_GUEST) == ("*", "config", "guest")
    sub = Subscription(name="a", chat_id=1)
    assert sub.tags == frozenset() and sub.broken is False and sub.source == SOURCE_CONFIG
    assert Subscription(name="a", chat_id=1, tags=frozenset({"t"})).with_broken().tags == frozenset({"t"})
