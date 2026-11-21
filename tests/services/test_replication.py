import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from support import Clock, Collector, FakeSlot, slot_factory, spec, table

from sa_home_node.plugins import ReplicationHooks
from sa_home_node.proto.wire import (
    ERR_BAD_REQUEST,
    ERR_UNAVAILABLE,
    MSG_GET_STATE,
    Address,
    Envelope,
    ProtoError,
    make_error_response,
    make_event,
    make_response,
)
from sa_home_node.services.events import EVENT_INSTANCE_CONFIG_CHANGED
from sa_home_node.services.instances import InstanceMeta, InstanceStore, content_hash
from sa_home_node.services.replication import ACTION_GET_INSTANCE_CONFIG, INSTANCES_KEY, ConfigReplicator
from sa_home_node.services.supervisor import Supervisor

PACKAGE = 'token = "aaa"\n# заметка\n'.encode()
NEWER = b'token = "bbb"\n'
EXTRA = b'[[item]]\nname = "x"\n'
EARLY = "2026-07-26T10:00:00+00:00"
LATE = "2026-07-27T10:00:00+00:00"
TABLE = table(spec("svc", instanced=True, satellites=(".extra",)), spec("plain"))


def meta_of(data: bytes, *, rev: int = 1, node: str = "node-b", instance: str = "one", at: str = EARLY) -> InstanceMeta:
    return InstanceMeta(service="svc", instance=instance, rev=rev, hash=content_hash(data), updated_at=at, origin_node=node)


class FakePeer:
    def __init__(
        self,
        packages: dict[str, tuple[InstanceMeta, bytes]] | None = None,
        *,
        alive: bool = True,
        state_error: Exception | None = None,
        lie: bool = False,
        listing: list | None = None,
        odd: dict | None = None,
    ) -> None:
        self.alive = alive
        self.packages = packages or {}
        self.state_error = state_error
        self.lie = lie
        self.listing = listing
        self.odd = odd
        self.asked: list[str] = []

    async def forward(self, env: Envelope) -> Envelope:
        if self.odd is not None:
            return make_response(env, self.odd)
        if env.type == MSG_GET_STATE:
            self.asked.append("get_state")
            if self.state_error is not None:
                raise self.state_error
            listing = [m.to_dict() for m, _ in self.packages.values()] if self.listing is None else self.listing
            return make_response(env, {INSTANCES_KEY: listing})
        assert env.payload["action"] == ACTION_GET_INSTANCE_CONFIG
        self.asked.append(ACTION_GET_INSTANCE_CONFIG)
        entry = self.packages.get(env.payload["args"]["instance"])
        if entry is None:
            return make_error_response(env.id, ERR_BAD_REQUEST, "нет")
        meta, data = entry
        return make_response(env, {"meta": meta.to_dict(), "content": (NEWER if self.lie else data).decode()})


def build(
    tmp_path,
    *texts: str,
    peers: dict[str, FakePeer] | None = None,
    hooks: tuple[ReplicationHooks, ...] = (),
    running: tuple[str, ...] = (),
    **kw: Any,
) -> tuple[ConfigReplicator, InstanceStore, dict[str, FakeSlot], Collector, list]:
    slots: dict[str, FakeSlot] = {}
    events, handlers = Collector(), []
    store = InstanceStore(tmp_path / "instances", "node-a")
    router = SimpleNamespace(peers=peers) if peers is not None else None
    rep = ConfigReplicator(
        "node-a",
        store,
        Supervisor(texts, TABLE, slot_factory(slots, running)),
        TABLE,
        router=router,
        emit=events,
        subscribe=handlers.append,
        hooks=hooks,
        sleep=Clock().sleep,
        **kw,
    )
    return rep, store, slots, events, handlers


def write_package(store: InstanceStore, data: bytes, instance: str = "one") -> None:
    path = store.package_path("svc", instance)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def changed(meta: InstanceMeta | dict, src: str = "node-b", kind: str = EVENT_INSTANCE_CONFIG_CHANGED) -> Envelope:
    payload = meta if isinstance(meta, dict) else meta.to_dict()
    return make_event(kind, payload, src=Address(node=src, service="node"))


async def test_local_edit_is_announced_once_and_restarts_active_slot(tmp_path) -> None:
    rep, store, slots, events, _ = build(tmp_path, "svc@one", running=("svc@one",))
    write_package(store, PACKAGE)
    await rep.announce_local_changes()
    assert [(n, d["rev"], d["origin_node"]) for n, d in events.events] == [(EVENT_INSTANCE_CONFIG_CHANGED, 1, "node-a")]
    assert slots["svc@one"].trace == ["restart"]
    events.events.clear()
    await rep.announce_local_changes()
    assert events.events == []


async def test_local_satellite_edit_announces_without_restart(tmp_path) -> None:
    rep, store, slots, events, _ = build(tmp_path, "svc@one", running=("svc@one",))
    write_package(store, EXTRA, "one.extra")
    await rep.announce_local_changes()
    assert slots["svc@one"].trace == []
    assert [n for n, _ in events.events] == [EVENT_INSTANCE_CONFIG_CHANGED]


async def test_before_announce_error_does_not_break_cycle(tmp_path, caplog) -> None:
    called: list[str] = []

    async def broken() -> None:
        called.append("broken")
        raise RuntimeError("сбой")

    async def fine() -> None:
        called.append("fine")

    hooks = (ReplicationHooks(before_announce=broken), ReplicationHooks(before_announce=fine))
    rep, store, _, events, _ = build(tmp_path, "svc@one", peers={}, hooks=hooks)
    write_package(store, PACKAGE)
    with caplog.at_level(logging.ERROR):
        await rep.tick()
    assert (called, len(events.events)) == (["broken", "fine"], 1)
    assert "ошибка подготовки" in caplog.text


@pytest.mark.parametrize("text", ["svc@one", "svc@one:standby"])
async def test_announced_revision_is_pulled_in_any_role(tmp_path, text: str) -> None:
    meta = meta_of(PACKAGE)
    rep, store, _, _, _ = build(tmp_path, text, peers={"node-b": FakePeer({"one": (meta, PACKAGE)})})
    await rep.on_event(changed(meta))
    assert store.read_package("svc", "one") == PACKAGE
    assert store.read_meta("svc", "one") == meta


async def test_ignored_events_pull_nothing(tmp_path) -> None:
    meta = meta_of(PACKAGE)
    peer = FakePeer({"one": (meta, PACKAGE), "two": (meta_of(PACKAGE, instance="two"), PACKAGE)})
    rep, store, _, _, _ = build(tmp_path, "svc@one", "plain", peers={"node-b": peer})
    ignored = [
        changed(meta, kind="other_event"),
        changed(meta_of(PACKAGE, instance="two")),
        changed({}),
        changed(meta_of(PACKAGE, node="node-a")),
        changed({"service": "svc", "instance": "one", "rev": "x"}),
    ]
    for env in ignored:
        await rep.on_event(env)
    assert peer.asked == []
    write_package(store, PACKAGE)
    store.refresh_all()
    await rep.on_event(changed(meta_of(PACKAGE, rev=1)))
    assert peer.asked == []


async def test_source_falls_back_to_event_sender(tmp_path) -> None:
    meta = meta_of(PACKAGE, node="")
    rep, store, _, _, _ = build(tmp_path, "svc@one", peers={"node-c": FakePeer({"one": (meta, PACKAGE)})})
    await rep.on_event(changed(meta, src="node-c"))
    assert store.read_package("svc", "one") == PACKAGE


async def test_catch_up_pulls_newer_and_skips_sleeping(tmp_path) -> None:
    meta = meta_of(NEWER, rev=7)
    live, asleep = FakePeer({"one": (meta, NEWER)}), FakePeer({"one": (meta, NEWER)}, alive=False)
    peers = {
        "a": asleep,
        "b": FakePeer(state_error=ProtoError(ERR_UNAVAILABLE, "нет")),
        "c": FakePeer(state_error=TimeoutError()),
        "d": live,
    }
    rep, store, _, _, _ = build(tmp_path, "svc@one:standby", peers=peers)
    store.apply(meta_of(PACKAGE, rev=2), PACKAGE)
    await rep.sync_from_peers()
    assert (store.read_package("svc", "one"), store.read_meta("svc", "one").rev) == (NEWER, 7)
    assert (asleep.asked, live.asked) == ([], ["get_state", ACTION_GET_INSTANCE_CONFIG])


async def test_catch_up_idle_without_instances_or_router(tmp_path) -> None:
    peer = FakePeer({"one": (meta_of(PACKAGE), PACKAGE)})
    rep, *_ = build(tmp_path, "plain", peers={"node-b": peer})
    await rep.sync_from_peers()
    assert peer.asked == []
    await build(tmp_path, "svc@one")[0].sync_from_peers()


async def test_failed_pull_is_left_for_next_cycle(tmp_path, caplog) -> None:
    rep, store, _, _, _ = build(tmp_path, "svc@one", peers={"node-b": FakePeer()})
    with caplog.at_level(logging.WARNING):
        await rep._pull(meta_of(PACKAGE), "node-b")
        await rep._pull(meta_of(PACKAGE), "node-z")
    assert store.read_package("svc", "one") is None
    assert "отказала" in caplog.text


async def test_passive_package_pulled_with_force_and_hooks(tmp_path) -> None:
    applied: list[bytes] = []

    def on_applied(meta: InstanceMeta, data: bytes) -> None:
        applied.append(data)
        raise RuntimeError("сбой")

    hooks = (
        ReplicationHooks(passive=lambda s, i: (s, i) == ("svc", "node-b"), on_applied=on_applied),
        ReplicationHooks(on_applied=lambda m, d: applied.append(b"second")),
    )
    fresh = meta_of(NEWER, node="node-b", instance="node-b", at=LATE)
    peer = FakePeer({"node-b": (fresh, NEWER)})
    rep, store, _, _, _ = build(tmp_path, "plain", peers={"node-b": peer}, hooks=hooks)
    store.apply(meta_of(PACKAGE, rev=5, instance="node-b"), PACKAGE)
    await rep.sync_from_peers()
    assert (store.read_package("svc", "node-b"), store.read_meta("svc", "node-b").rev) == (NEWER, 1)
    assert applied == [NEWER, b"second"]
    peer.asked.clear()
    await rep.sync_from_peers()
    assert peer.asked == ["get_state"]


async def test_passive_without_local_copy_and_non_authoritative(tmp_path) -> None:
    hooks = (ReplicationHooks(passive=lambda s, i: i == "node-b"),)
    first = meta_of(PACKAGE, instance="node-b")
    relayed = meta_of(PACKAGE, node="node-c", instance="node-b")
    rep, store, *_ = build(tmp_path, "plain", peers={}, hooks=hooks)
    assert rep._wins(first, None)
    store.apply(first, PACKAGE)
    assert not rep._wins(relayed, store.read_meta("svc", "node-b"))
    assert not rep._wins(first, store.read_meta("svc", "node-b"))


@pytest.mark.parametrize(
    ("text", "running", "expect"),
    [("svc@one", True, ["restart"]), ("svc@one", False, []), ("svc@one:standby", True, [])],
)
async def test_restart_only_active_running_slot(tmp_path, text, running, expect) -> None:
    meta = meta_of(NEWER, rev=2)
    rep, _, slots, _, _ = build(
        tmp_path, text, peers={"node-b": FakePeer({"one": (meta, NEWER)})}, running=("svc@one",) if running else ()
    )
    await rep.on_event(changed(meta))
    assert slots["svc@one"].trace == expect


async def test_incoming_satellite_is_pulled_and_restarts_owner(tmp_path) -> None:
    meta = meta_of(EXTRA, instance="one.extra")
    peers = {"node-b": FakePeer({"one.extra": (meta, EXTRA)})}
    rep, store, slots, _, _ = build(tmp_path, "svc@one", peers=peers, running=("svc@one",))
    await rep.on_event(changed(meta))
    assert store.read_package("svc", "one.extra") == EXTRA
    assert slots["svc@one"].trace == ["restart"]


async def test_foreign_hash_is_rejected(tmp_path) -> None:
    meta = meta_of(PACKAGE, rev=3)
    peers = {"node-b": FakePeer({"one": (meta, PACKAGE)}, lie=True)}
    rep, store, slots, _, _ = build(tmp_path, "svc@one", peers=peers, running=("svc@one",))
    await rep.on_event(changed(meta))
    assert store.read_package("svc", "one") is None
    assert slots["svc@one"].trace == []


@pytest.mark.parametrize("odd", [{"content": 5}, {INSTANCES_KEY: 5}])
async def test_malformed_peer_replies_are_ignored(tmp_path, odd) -> None:
    rep, store, *_ = build(tmp_path, "svc@one", peers={"node-b": FakePeer(odd=odd)})
    await rep.on_event(changed(meta_of(PACKAGE)))
    await rep.sync_from_peers()
    assert store.read_package("svc", "one") is None and store.known() == []


async def test_serving_replies(tmp_path) -> None:
    rep, store, *_ = build(tmp_path, "svc@one")
    write_package(store, PACKAGE)
    write_package(store, EXTRA, "two")
    await rep.announce_local_changes()
    payload = rep.get_instance_config({"service": "svc", "instance": "one"})
    assert (payload["content"].encode(), payload["meta"]["rev"]) == (PACKAGE, 1)
    listed = rep.list_instances({"service": "svc"})
    assert listed["service"] == "svc"
    assert [m["instance"] for m in listed[INSTANCES_KEY]] == ["one", "two"]
    assert rep.list_instances({"service": "other"})[INSTANCES_KEY] == []
    assert len(rep.local_revisions()) == 2
    with pytest.raises(ProtoError) as exc:
        rep.get_instance_config({"service": "svc", "instance": "zzz"})
    assert exc.value.code == ERR_BAD_REQUEST
    assert "node-a" in str(exc.value)


@pytest.mark.parametrize("instance", ["../x", "a/b", "..", "", "-x", ".hidden"])
async def test_get_instance_config_rejects_unsafe_names(tmp_path, instance: str) -> None:
    rep, *_ = build(tmp_path, "svc@one")
    with pytest.raises(ProtoError) as exc:
        rep.get_instance_config({"service": "svc", "instance": instance})
    assert exc.value.code == ERR_BAD_REQUEST


@pytest.mark.parametrize(
    "bad",
    [
        {"service": "svc", "instance": "one", "rev": "много"},
        {"service": "svc", "instance": "one", "rev": None},
        {"service": "svc", "instance": "../one", "rev": 9},
        "строка",
        None,
    ],
)
async def test_broken_peer_record_is_skipped(tmp_path, bad, caplog) -> None:
    good = meta_of(NEWER, rev=7)
    peer = FakePeer({"one": (good, NEWER)}, listing=[bad, good.to_dict()])
    rep, store, *_ = build(tmp_path, "svc@one", peers={"node-b": peer})
    with caplog.at_level(logging.WARNING):
        await rep.sync_from_peers()
    assert store.read_meta("svc", "one") == good
    assert "битую запись" in caplog.text


async def test_start_subscribes_ticks_and_stops(tmp_path) -> None:
    rep, store, _, events, handlers = build(tmp_path, "svc@one", peers={})
    write_package(store, PACKAGE)
    await rep.start()
    assert (handlers, len(events.events)) == ([rep.on_event], 1)
    assert rep._task is not None
    await rep.stop()
    assert rep._task is None
    await rep.stop()


async def test_run_loops_and_survives_tick_error(tmp_path, caplog) -> None:
    rep, *_ = build(tmp_path, "svc@one", poll_interval_s=3.0)
    calls = 0

    async def tick() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("сбой")
        raise asyncio.CancelledError

    rep.tick = tick
    with caplog.at_level(logging.ERROR), pytest.raises(asyncio.CancelledError):
        await rep._run()
    assert calls == 2
    assert "ошибка цикла" in caplog.text
