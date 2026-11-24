from __future__ import annotations

import asyncio

import pytest

from sa_home_node.proto.wire import Address, ProtoError
from sa_home_node.wake import peers
from sa_home_node.wake.peers import (
    MemoryWakeCache,
    collect_reports,
    fetch_state,
    find_lan_waker,
    resolve_wake_info,
)

WAKE_A = {"mac": "aa:bb:cc:dd:ee:01", "broadcast": "10.0.0.255"}
WAKE_B = {"mac": "aa:bb:cc:dd:ee:02", "broadcast": "10.0.0.255"}


class FakeLink:
    def __init__(self, own=None, states=None):
        self.own = own
        self.states = states or {}
        self.asked = []

    async def get_state(self, dst=None):
        self.asked.append(dst)
        if dst is None:
            if self.own is None:
                raise ProtoError("unavailable", "нет")
            return self.own
        value = self.states.get(dst.node)
        if value is None:
            raise ProtoError("unavailable", "нет")
        if isinstance(value, Exception):
            raise value
        return value

    async def command(self, action, args=None, dst=None, *, timeout=None):
        return {}


def own_state(peers):
    return {"node": "me", "kind": "k", "peers": peers}


async def test_fetch_state_ok_and_errors():
    link = FakeLink(states={"x": {"a": 1}, "t": TimeoutError(), "c": ConnectionError()})
    assert await fetch_state(link, Address(node="x", service="node")) == {"a": 1}
    assert await fetch_state(link, Address(node="t", service="node")) is None
    assert await fetch_state(link, Address(node="c", service="node")) is None
    assert await fetch_state(link, Address(node="none", service="node")) is None


async def test_fetch_state_timeout_read_at_call(monkeypatch):
    class Slow(FakeLink):
        async def get_state(self, dst=None):
            await asyncio.sleep(1)
            return {}

    monkeypatch.setattr(peers, "PEER_TIMEOUT_S", 0.01)
    assert await fetch_state(Slow(), None) is None


async def test_collect_reports_alive_dead_left():
    own = own_state(
        [
            {"id": "live", "alive": True, "kind": "srv"},
            {"id": "dead", "alive": False},
            {"id": "gone", "alive": False, "left": True},
        ]
    )
    link = FakeLink(own=own, states={"live": {"node": "live", "wake": WAKE_A}, "dead": {"x": 1}})
    reports = await collect_reports(link, own)
    by = {r.node_id: r for r in reports}
    assert by["me"].state is own and by["me"].alive and by["me"].kind == "k"
    assert by["live"].state["wake"] == WAKE_A and by["live"].kind == "srv"
    assert by["dead"].state is None and not by["dead"].alive
    assert by["gone"].left and by["gone"].state is None
    assert [a.node for a in link.asked if a] == ["live"]


async def test_collect_reports_with_monitor():
    own = own_state([{"id": "live", "alive": True}])
    link = FakeLink(own=own, states={"live": {"s": 1}})
    reports = await collect_reports(link, own, with_monitor=True)
    assert {(a.node, a.service) for a in link.asked if a} == {
        ("live", "node"),
        ("live", "monitor"),
        ("me", "monitor"),
    }
    assert reports[1].monitor == {"s": 1}


async def test_cache_put_empty_is_noop():
    cache = MemoryWakeCache()
    await cache.put("n", None)
    await cache.put("n", {})
    await cache.put("n", {"broadcast": "1.1.1.255"})
    assert await cache.get("n") is None
    await cache.put("n", WAKE_A)
    assert await cache.get("n") == WAKE_A


async def test_resolve_hit_does_not_ask():
    cache = MemoryWakeCache()
    await cache.put("t", WAKE_A)
    link = FakeLink(own=None)
    assert await resolve_wake_info(link, cache, "t") == WAKE_A
    assert link.asked == []


async def test_resolve_miss_fills_cache():
    cache = MemoryWakeCache()
    link = FakeLink(own=own_state([{"id": "t", "wake": WAKE_A}, {"id": "o"}]))
    assert await resolve_wake_info(link, cache, "t") == WAKE_A
    assert await cache.get("t") == WAKE_A


@pytest.mark.parametrize(
    "own",
    [None, own_state([]), own_state([{"id": "t"}]), own_state([{"id": "t", "wake": {"x": 1}}])],
)
async def test_resolve_unknown(own):
    assert await resolve_wake_info(FakeLink(own=own), MemoryWakeCache(), "t") is None


async def test_find_waker_same_segment_and_caches():
    own = own_state([{"id": "t", "alive": False}, {"id": "w", "alive": True}, {"id": "far", "alive": True}])
    link = FakeLink(
        own=own,
        states={"w": {"wake": WAKE_B}, "far": {"wake": {"mac": "m", "broadcast": "9.9.9.255"}}},
    )
    cache = MemoryWakeCache()
    assert await find_lan_waker(link, cache, "t", "10.0.0.255") == "w"
    assert await cache.get("w") == WAKE_B
    assert await cache.get("far") is not None


async def test_find_waker_own_wake_counts():
    own = {"node": "me", "wake": WAKE_A, "peers": [{"id": "t", "alive": False}]}
    assert await find_lan_waker(FakeLink(own=own), MemoryWakeCache(), "t", "10.0.0.255") == "me"


async def test_find_waker_none_cases():
    cache = MemoryWakeCache()
    assert await find_lan_waker(FakeLink(own=None), cache, "t", "b") is None
    own = own_state([{"id": "w", "alive": True}, {"id": "d", "alive": False}])
    link = FakeLink(own=own, states={"w": {"wake": WAKE_A}})
    assert await find_lan_waker(link, cache, "t", "other") is None
    assert await find_lan_waker(link, cache, "w", "10.0.0.255") is None
    link = FakeLink(own=own, states={})
    assert await find_lan_waker(link, cache, "t", "10.0.0.255") is None
