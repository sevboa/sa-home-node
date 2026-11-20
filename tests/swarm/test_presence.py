from __future__ import annotations

import time
from pathlib import Path

import pytest
from support import wait_for

from sa_home_node.swarm.events import EVENT_NODE_DOWN, EVENT_NODE_RETURNED, EVENT_NODE_UP
from sa_home_node.swarm.kind import KIND_VPS, KIND_WORKSTATION
from sa_home_node.swarm.link import PeerLink
from sa_home_node.swarm.presence import PresenceWatcher
from sa_home_node.swarm.router import NodeRouter
from sa_home_node.swarm.state import NodeState

DOWN_VPS_EVENT = (EVENT_NODE_DOWN, {"node": "node-c", "kind": KIND_VPS, "down_s": 600})


def peer(name: str, *, kind: str = "", down_s: float | None = None) -> PeerLink:
    link = PeerLink(name, "tcp://127.0.0.1:1")
    link.node_kind = kind
    if down_s is None:
        link._client = object()
    else:
        link.down_since = time.monotonic() - down_s
    return link


def watcher(node_id: str, *links: PeerLink, **kw) -> tuple[PresenceWatcher, list]:
    events: list = []
    router = NodeRouter(node_id, peers={link.name: link for link in links})

    async def emit(kind: str, data: dict) -> None:
        events.append((kind, data))

    return PresenceWatcher(node_id, router, emit=emit, **kw), events


async def test_sleeping_workstation_silent() -> None:
    w, events = watcher("node-a", peer("node-c", kind=KIND_WORKSTATION, down_s=9999))
    await w.check_once()
    assert events == []


async def test_down_then_up() -> None:
    link = peer("node-c", kind=KIND_VPS, down_s=600)
    w, events = watcher("node-a", link)
    await w.check_once()
    await w.check_once()
    assert events == [DOWN_VPS_EVENT]
    link._client = object()
    link.down_since = None
    await w.check_once()
    assert events == [DOWN_VPS_EVENT, (EVENT_NODE_UP, {"node": "node-c", "kind": KIND_VPS})]


async def test_below_threshold_silent() -> None:
    w, events = watcher("node-a", peer("node-c", kind=KIND_VPS, down_s=120), down_after_s=300)
    await w.check_once()
    assert events == []


async def test_left_silent() -> None:
    link = peer("node-c", kind=KIND_VPS, down_s=600)
    link.left = True
    w, events = watcher("node-a", link)
    await w.check_once()
    assert events == []


async def test_workstation_returned() -> None:
    link = peer("node-c", kind=KIND_WORKSTATION, down_s=100)
    w, events = watcher("node-a", link)
    await w.check_once()
    link._client = object()
    link.down_since = None
    await w.check_once()
    assert events == [(EVENT_NODE_RETURNED, {"node": "node-c", "kind": KIND_WORKSTATION})]


async def test_alive_silent() -> None:
    w, events = watcher("node-a", peer("node-b"), peer("node-c"))
    await w.check_once()
    await w.check_once()
    assert events == []


@pytest.mark.parametrize(
    ("node_id", "expected"),
    [("node-z", []), ("node-a", [DOWN_VPS_EVENT])],
)
async def test_announcer(node_id: str, expected: list) -> None:
    w, events = watcher(node_id, peer("node-c", kind=KIND_VPS, down_s=600), peer("node-b"))
    await w.check_once()
    assert events == expected


async def test_kind_persisted(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    w, _ = watcher("node-a", peer("node-b", kind=KIND_VPS), state=NodeState(), state_path=path)
    await w.check_once()
    state = NodeState.load(path)
    assert [(p.id, p.kind) for p in state.peers] == [("node-b", KIND_VPS)]
    w2, events = watcher("node-a", peer("node-b", down_s=600), state=state)
    await w2.check_once()
    assert events == [(EVENT_NODE_DOWN, {"node": "node-b", "kind": KIND_VPS, "down_s": 600})]


async def test_background() -> None:
    w, events = watcher("node-a", peer("node-c", kind=KIND_VPS, down_s=600), poll_interval_s=0.01)
    await w.start()
    try:
        assert await wait_for(lambda: events != [])
    finally:
        await w.stop()
    assert events == [DOWN_VPS_EVENT]
