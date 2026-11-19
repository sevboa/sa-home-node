from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from sa_home_node.proto.wire import Address, Envelope, make_event
from sa_home_node.swarm.events import (
    EVENT_NODE_JOINED,
    EVENT_NODE_LEAVING,
    EVENT_RESTART_APPLIED,
)
from sa_home_node.swarm.link import PeerLink
from sa_home_node.swarm.relay import (
    MAX_EVENT_HOPS,
    EventRelay,
    SeenEvents,
    announce_version,
)
from sa_home_node.swarm.router import NodeRouter
from sa_home_node.swarm.state import NodeState


def make_relay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    num_handlers: int = 0,
) -> tuple[EventRelay, list[str], NodeRouter, NodeState]:
    log_: list[str] = []
    router = NodeRouter("node-b")
    state = NodeState()
    state_path = tmp_path / "state.json"

    async def fake_add_peer(link: PeerLink) -> None:
        log_.append(f"add:{link.name}")
        router.peers[link.name] = link

    monkeypatch.setattr(router, "add_peer", fake_add_peer)

    async def fake_broadcast(env: Envelope) -> int:
        log_.append(f"send:{env.id}:{env.hops}")
        return 1

    def handler_factory(n: int) -> Any:
        async def _handler(env: Envelope) -> None:
            log_.append(f"handler:{n}")

        return _handler

    relay = EventRelay(
        "node-b",
        router=router,
        state=state,
        state_path=state_path,
        make_peer_link=PeerLink,
        broadcast=fake_broadcast,
        handlers=[handler_factory(i) for i in range(num_handlers)],
    )
    return relay, log_, router, state


def test_seen_events_capacity() -> None:
    seen = SeenEvents(maxsize=3)
    for i in range(5):
        seen.seen(f"id{i}")
    assert seen.seen("id0") is False
    assert seen.seen("id4") is True


def _joined_env(node_from: str, node_new: str, endpoint: str, endpoints: list[str] | None = None) -> Envelope:
    data: dict[str, Any] = {"node_id": node_new, "endpoint": endpoint}
    if endpoints:
        data["endpoints"] = endpoints
    return make_event(EVENT_NODE_JOINED, data, src=Address(node=node_from))


def _leaving_env(node_from: str, leaving: str | None = None) -> Envelope:
    data = {"node": leaving} if leaving else {}
    return make_event(EVENT_NODE_LEAVING, data, src=Address(node=node_from))


@pytest.mark.asyncio
async def test_node_joined_auto_connect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    relay, log_, router, _ = make_relay(tmp_path, monkeypatch)
    env = _joined_env("node-a", "node-c", "tcp://127.0.0.1:8710")
    await relay.on_event(env)
    assert "add:node-c" in log_
    assert str(router.peers["node-c"].endpoint) == "tcp://127.0.0.1:8710"
    assert NodeState.load(tmp_path / "state.json").peers[0].id == "node-c"


@pytest.mark.asyncio
async def test_node_joined_with_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    relay, log_, router, _ = make_relay(tmp_path, monkeypatch)
    extra = ["tcp://127.0.0.1:8711", "tcp://127.0.0.1:8712"]
    env = _joined_env("node-a", "node-c", "tcp://127.0.0.1:8710", extra)
    await relay.on_event(env)
    assert "add:node-c" in log_
    assert router.peers["node-c"].endpoints == [
        "tcp://127.0.0.1:8710",
        "tcp://127.0.0.1:8711",
        "tcp://127.0.0.1:8712",
    ]


@pytest.mark.asyncio
async def test_own_echo_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    relay, log_, _, _ = make_relay(tmp_path, monkeypatch)
    await relay.on_event(_joined_env("node-b", "node-c", "tcp://127.0.0.1:8710"), local=False)
    assert not log_


@pytest.mark.asyncio
async def test_local_service_event_relayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    relay, log_, _, _ = make_relay(tmp_path, monkeypatch)
    env = make_event(
        EVENT_NODE_JOINED,
        {"node_id": "node-c", "endpoint": "tcp://127.0.0.1:8710"},
        src=Address(node="node-b", service="tasks"),
    )
    await relay.on_event(env, local=True)
    assert any(e.startswith("send:") for e in log_)


@pytest.mark.parametrize("node_new", ["node-c", "node-b"])
@pytest.mark.asyncio
async def test_node_joined_ignored_known_or_self(
    node_new: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    relay, log_, router, _ = make_relay(tmp_path, monkeypatch)
    if node_new == "node-c":
        router.peers["node-c"] = PeerLink("node-c", "tcp://127.0.0.1:8710")
    await relay.on_event(_joined_env("node-a", node_new, "tcp://127.0.0.1:8710"))
    assert f"add:{node_new}" not in log_


@pytest.mark.asyncio
async def test_deduplication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    relay, log_, _, _ = make_relay(tmp_path, monkeypatch, num_handlers=1)
    env = _joined_env("node-a", "node-c", "tcp://127.0.0.1:8710")
    for _ in range(3):
        await relay.on_event(env)
    assert sum(1 for e in log_ if e.startswith("send:")) == 1
    assert sum(1 for e in log_ if e.startswith("handler:")) == 1


@pytest.mark.asyncio
async def test_retransmission_hops_increment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    relay, log_, _, _ = make_relay(tmp_path, monkeypatch)
    await relay.on_event(_joined_env("node-a", "node-c", "tcp://127.0.0.1:8710"))
    assert next(e for e in log_ if e.startswith("send:")).endswith(":1")


@pytest.mark.asyncio
async def test_max_hops_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    relay, log_, _, _ = make_relay(tmp_path, monkeypatch)
    env = dataclasses.replace(_joined_env("node-a", "node-c", "tcp://127.0.0.1:8710"), hops=MAX_EVENT_HOPS)
    await relay.on_event(env)
    assert not any(e.startswith("send:") for e in log_)


@pytest.mark.asyncio
async def test_node_leaving_marks_left(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    relay, log_, router, _ = make_relay(tmp_path, monkeypatch)
    link = PeerLink("node-c", "tcp://127.0.0.1:8710")
    router.peers["node-c"] = link
    original = link.note_left
    monkeypatch.setattr(link, "note_left", lambda: (log_.append(f"left:{link.name}"), original()))
    await relay.on_event(_leaving_env("node-a", "node-c"))
    assert link.left is True
    kinds = [e.split(":")[0] for e in log_]
    assert kinds.index("send") < kinds.index("left")


@pytest.mark.asyncio
async def test_node_leaving_without_data_uses_src(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    relay, _, router, _ = make_relay(tmp_path, monkeypatch)
    link = PeerLink("node-a", "tcp://127.0.0.1:8710")
    router.peers["node-a"] = link
    await relay.on_event(_leaving_env("node-a"))
    assert link.left is True


@pytest.mark.asyncio
async def test_handlers_called_in_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    relay, log_, _, _ = make_relay(tmp_path, monkeypatch, num_handlers=2)
    await relay.on_event(_joined_env("node-a", "node-c", "tcp://127.0.0.1:8710"))
    assert [e for e in log_ if e.startswith("handler:")] == ["handler:0", "handler:1"]


@pytest.mark.asyncio
async def test_handler_exception_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[int] = []
    async def bad(_: Envelope) -> None:
        calls.append(0)
        raise RuntimeError("boom")
    async def good(_: Envelope) -> None:
        calls.append(1)
    relay, _, _, _ = make_relay(tmp_path, monkeypatch)
    relay.handlers = [bad, good]
    with caplog.at_level("ERROR"):
        await relay.on_event(_joined_env("node-a", "node-c", "tcp://127.0.0.1:8710"))
    assert "Ошибка в обработчике события" in caplog.text
    assert calls == [0, 1]


def _broadcast_capture(sent: list[tuple[str, dict]]) -> Any:
    async def _bcast(event: str, data: dict) -> int:
        sent.append((event, data))
        return 1
    return _bcast


@pytest.mark.parametrize("last_ver,version,expect_sent,check_saved", [
    (None, "1.0.0", [], True),
    ("1.0.0", "1.0.0", [], False),
    ("0.9.0", "1.0.0", [(EVENT_RESTART_APPLIED, {"from": "0.9.0", "to": "1.0.0"})], True),
])
@pytest.mark.asyncio
async def test_announce_version_scenarios(
    last_ver: str | None, version: str, expect_sent: list, check_saved: bool, tmp_path: Path
) -> None:
    state = NodeState(last_known_version=last_ver) if last_ver else NodeState()
    state_path = tmp_path / "state.json"
    sent: list[tuple[str, dict]] = []
    await announce_version(state, state_path, _broadcast_capture(sent), version=version)
    assert sent == expect_sent
    if check_saved:
        assert NodeState.load(state_path).last_known_version == version


@pytest.mark.asyncio
async def test_announce_version_retries_until_delivered(tmp_path: Path) -> None:
    state = NodeState(last_known_version="0.9.0")
    state_path = tmp_path / "state.json"
    calls = 0
    async def bcast(event: str, data: dict) -> int:
        nonlocal calls
        calls += 1
        return 1 if calls >= 4 else 0
    await announce_version(state, state_path, bcast, version="1.0.0", retry_s=0.01, window_s=1.0)
    assert calls == 4


@pytest.mark.asyncio
async def test_announce_version_timeout_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    state = NodeState(last_known_version="0.9.0")
    state_path = tmp_path / "state.json"
    calls = 0
    async def bcast(event: str, data: dict) -> int:
        nonlocal calls
        calls += 1
        return 0
    with caplog.at_level("WARNING"):
        await announce_version(state, state_path, bcast, version="1.0.0", retry_s=0.01, window_s=0.03)
    assert calls >= 2
    assert "не удалось разослать" in caplog.text
    assert NodeState.load(state_path).last_known_version == "1.0.0"
