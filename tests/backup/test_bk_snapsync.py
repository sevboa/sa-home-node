from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from sa_home_node.backup import snapsync
from sa_home_node.backup.config import BackupConfig
from sa_home_node.backup.snapdoc import ACTION_GET_SNAPSHOT, ACTION_SNAPSHOT_POKE, SnapshotError
from sa_home_node.backup.spec import BackupSpec
from sa_home_node.proto.wire import ERR_BAD_REQUEST, ProtoError

FMT = "test/snapshot/1"
KEY = "A" * 43 + "="
FIXTURES = Path(__file__).parent.parent / "fixtures" / "backup"


async def _noop(*_a, **_k):
    return {}


def make_spec() -> BackupSpec:
    return BackupSpec(
        service="svc",
        identity_service="svc-identity",
        identity_format="test/identity/1",
        snapshot_format=FMT,
        bundle_format="test/bundle/1",
        tables=("tbl_a",),
        skip_tables=frozenset(),
        primary_table="tbl_a",
        trigger_actions=frozenset(),
        collect_identity=_noop,
        apply=_noop,
        summarize=lambda b: [],
        rows_extra=lambda t: {},
    )


@dataclass
class Stored:
    meta: dict[str, Any]


@dataclass
class FakeStore:
    saved: list[tuple[str, bytes, dict]] = field(default_factory=list)
    current: Stored | None = None

    def load_snapshot(self, node):
        return self.current

    def save_snapshot(self, node, blob, meta):
        self.saved.append((node, blob, meta))


class FakeAsk:
    def __init__(self, response):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, action, args):
        self.calls.append((action, args))
        return self.response


def cfg(**kw) -> BackupConfig:
    return BackupConfig(partner="node-a", **kw)


def reply(**meta):
    base = {"source": "node-a", "format": FMT, "hash": "sha256:new"}
    return {"sealed": base64.b64encode(b"blob").decode(), "meta": {**base, **meta}}


async def test_pull_saves_new_hash():
    store, ask = FakeStore(), FakeAsk(reply())
    assert await snapsync.SnapshotReceiver(make_spec(), cfg(), store, ask).pull_once()
    assert store.saved == [("node-a", b"blob", {"source": "node-a", "format": FMT, "hash": "sha256:new"})]
    assert ask.calls == [(ACTION_GET_SNAPSHOT, {"have_hash": None})]


@pytest.mark.parametrize(
    "response, have",
    [
        ({"unchanged": True, "hash": "sha256:old"}, "sha256:old"),
        (reply(hash="sha256:old"), "sha256:old"),
        (reply(hash=""), None),
    ],
)
async def test_pull_not_saved(response, have):
    store = FakeStore(current=Stored({"hash": have}) if have else None)
    ask = FakeAsk(response)
    assert not await snapsync.SnapshotReceiver(make_spec(), cfg(), store, ask).pull_once()
    assert store.saved == []
    assert ask.calls[0][1] == {"have_hash": have}


@pytest.mark.parametrize(
    "response",
    [
        {"meta": {}},
        {"sealed": "!!!", "meta": {}},
        {"sealed": "YQ==", "meta": 5},
        reply(source="node-b"),
        reply(format="other"),
    ],
)
async def test_pull_rejects(response):
    store = FakeStore()
    with pytest.raises(SnapshotError):
        await snapsync.SnapshotReceiver(make_spec(), cfg(), store, FakeAsk(response)).pull_once()
    assert store.saved == []


async def test_receiver_loop_pulls_on_poke_and_survives_errors():
    store = FakeStore()
    calls = 0

    async def ask(action, args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("недоступен")
        return reply()

    receiver = snapsync.SnapshotReceiver(make_spec(), cfg(snapshot_poll_s=0.01), store, ask)
    await receiver.start()
    for _ in range(100):
        if store.saved:
            break
        await asyncio.sleep(0.01)
    await receiver.stop()
    assert calls >= 2 and len(store.saved) == 1
    await receiver.stop()


async def test_poke_wakes_loop():
    store, ask = FakeStore(), FakeAsk({"unchanged": True})
    receiver = snapsync.SnapshotReceiver(make_spec(), cfg(snapshot_poll_s=3600), store, ask)
    await receiver.start()
    receiver.poke()
    for _ in range(100):
        if ask.calls:
            break
        await asyncio.sleep(0.01)
    await receiver.stop()
    assert len(ask.calls) == 1


class FakeSource:
    def __init__(self):
        self.touched = 0
        self.stopped = False

    def touch(self):
        self.touched += 1

    async def handle_get(self, args):
        if args.get("bad"):
            raise SnapshotError("плохо")
        return {"hash": "h"}

    async def stop(self):
        self.stopped = True


def make_backup(*, source, receiver, store):
    return snapsync.SnapshotBackup(source, receiver, store=store, partner="node-a")


@pytest.mark.parametrize(
    "with_source, with_receiver, with_store, expected",
    [
        (False, False, False, []),
        (True, False, False, [ACTION_GET_SNAPSHOT]),
        (False, True, False, [ACTION_SNAPSHOT_POKE]),
        (True, True, False, [ACTION_GET_SNAPSHOT, ACTION_SNAPSHOT_POKE]),
    ],
)
def test_action_specs(with_source, with_receiver, with_store, expected):
    receiver = snapsync.SnapshotReceiver(make_spec(), cfg(), FakeStore(), FakeAsk({}))
    backup = make_backup(
        source=FakeSource() if with_source else None,
        receiver=receiver if with_receiver else None,
        store=FakeStore() if with_store else None,
    )
    assert [s.id for s in backup.action_specs()] == expected


def test_get_spec_shape():
    backup = make_backup(source=FakeSource(), receiver=None, store=None)
    (spec,) = backup.action_specs()
    assert spec.title == "💾 Запечатанный снапшот БД"
    (param,) = spec.params
    assert (param.name, param.required, param.title) == ("have_hash", False, "Хеш у запросившего")


async def test_handle_routes_and_errors():
    store = FakeStore()
    receiver = snapsync.SnapshotReceiver(make_spec(), cfg(), store, FakeAsk({}))
    source = FakeSource()
    backup = make_backup(source=source, receiver=receiver, store=store)
    assert await backup.handle(ACTION_GET_SNAPSHOT, {}) == {"hash": "h"}
    assert await backup.handle(ACTION_SNAPSHOT_POKE, {}) == {"ok": True}
    assert receiver._wake.is_set()
    assert await backup.handle("unknown", {}) is None
    with pytest.raises(ProtoError) as exc:
        await backup.handle(ACTION_GET_SNAPSHOT, {"bad": 1})
    assert (exc.value.code, exc.value.message) == (ERR_BAD_REQUEST, "плохо")
    backup.touch()
    assert source.touched == 1
    await backup.stop()
    assert source.stopped


async def test_handle_without_parts():
    backup = make_backup(source=None, receiver=None, store=None)
    assert await backup.handle(ACTION_GET_SNAPSHOT, {}) is None
    assert await backup.handle(ACTION_SNAPSHOT_POKE, {}) is None
    backup.touch()
    await backup.start()
    await backup.stop()


async def test_start_stop_delegate_to_receiver():
    receiver = snapsync.SnapshotReceiver(make_spec(), cfg(snapshot_poll_s=3600), FakeStore(), FakeAsk({}))
    backup = make_backup(source=None, receiver=receiver, store=None)
    await backup.start()
    assert receiver._task is not None
    await backup.stop()
    assert receiver._task is None


async def test_serve_error_becomes_proto_error(monkeypatch):
    def boom(store, partner, action, args):
        raise snapsync.serve.ServeError("нет копии")

    monkeypatch.setattr(snapsync.serve, "handle", boom)
    backup = make_backup(source=None, receiver=None, store=FakeStore())
    with pytest.raises(ProtoError) as exc:
        await backup.handle("backup_store_get", {})
    assert exc.value.message == "нет копии"


class RecordingSource:
    def __init__(self, spec, backup, state_path, conn_getter, node_id, *, notify=None):
        self.notify = notify
        self.args = (state_path, node_id)


def test_build_without_partner(tmp_path):
    assert snapsync.build_backup(make_spec(), BackupConfig(), tmp_path / "s.db", lambda: None, "n", FakeAsk({})) is None


async def test_build_source_needs_key(tmp_path, monkeypatch):
    monkeypatch.setattr(snapsync, "SnapshotSource", RecordingSource)
    ask = FakeAsk({"ok": True})
    state = tmp_path / "data" / "s.db"
    plain = snapsync.build_backup(make_spec(), cfg(), state, lambda: None, "n", ask)
    assert plain.source is None and plain.receiver is not None and plain.partner == "node-a"
    full = snapsync.build_backup(make_spec(), cfg(recipient_public_key=KEY), state, lambda: None, "n", ask)
    assert isinstance(full.source, RecordingSource) and full.source.args == (state, "n")
    await full.source.notify()
    assert ask.calls == [(ACTION_SNAPSHOT_POKE, {})]


def test_build_bad_key_warns(tmp_path, monkeypatch, caplog):
    def bad(*_a, **_k):
        raise snapsync.sealed.SealedError("мусор")

    monkeypatch.setattr(snapsync, "SnapshotSource", bad)
    result = snapsync.build_backup(
        make_spec(), cfg(recipient_public_key=KEY), tmp_path / "s.db", lambda: None, "n", FakeAsk({})
    )
    assert result.source is None and result.receiver is not None
    assert "негоден" in caplog.text


@pytest.mark.skipif(not (FIXTURES / "actions.json").exists(), reason="нет эталона actions.json")
def test_actions_fixture():
    data = json.loads((FIXTURES / "actions.json").read_text(encoding="utf-8"))
    assert data
