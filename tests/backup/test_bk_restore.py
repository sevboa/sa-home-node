from __future__ import annotations

import base64

import pytest

from sa_home_node.backup import restore, sealed
from sa_home_node.backup.bundle import RestoreError
from sa_home_node.backup.package import canonical_bytes
from sa_home_node.backup.snapdoc import seal_document
from sa_home_node.backup.spec import BackupSpec


def make_spec(**kw):
    async def nothing(*a):
        return {}

    base = {
        "service": "svc",
        "identity_service": "svc-identity",
        "identity_format": "svc/identity/1",
        "snapshot_format": "svc/snapshot/1",
        "bundle_format": "svc/restore/1",
        "tables": ("tbl_a", "tbl_b"),
        "skip_tables": frozenset({"tbl_b"}),
        "primary_table": "tbl_a",
        "trigger_actions": frozenset(),
        "collect_identity": nothing,
        "apply": nothing,
        "summarize": lambda bundle: ["  свой: строка"],
        "rows_extra": lambda tables: {"активных": 1},
    }
    return BackupSpec(**{**base, **kw})


def v(label, **meta):
    return {"label": label, "meta": meta}


@pytest.mark.parametrize(
    ("versions", "requested", "expected"),
    [
        ([], None, None),
        ([v("last_nonempty")], None, None),
        ([v("latest", empty=False)], None, "latest"),
        ([v("latest", empty=True)], None, "latest"),
        ([v("latest", empty=False), v("last_nonempty")], None, "latest"),
        ([v("latest", empty=True), v("last_nonempty")], "latest", "latest"),
        ([v("latest"), v("last_nonempty")], "last_nonempty", "last_nonempty"),
    ],
)
def test_pick_snapshot(versions, requested, expected):
    assert restore.pick_snapshot(versions, requested, "tbl_a") == expected


def test_pick_snapshot_empty_latest_refused():
    versions = [v("latest", empty=True), v("last_nonempty", rows={"tbl_a": 7})]
    with pytest.raises(RestoreError, match=r"latest пустой.*tbl_a.*строк: 7.*--snapshot last_nonempty"):
        restore.pick_snapshot(versions, None, "tbl_a")


def test_pick_snapshot_unknown_requested():
    with pytest.raises(RestoreError, match=r"нет снапшота 'x' \(есть: latest\)"):
        restore.pick_snapshot([v("latest")], "x", "tbl_a")
    with pytest.raises(RestoreError, match=r"\(есть: нет\)"):
        restore.pick_snapshot([], "x", "tbl_a")


class Keeper:
    def __init__(self, spec, node="node-a", snap_node="node-a", identity=True, snapshots=("latest",), empty=False):
        self.priv, pub = sealed.generate_keypair()
        self.spec, self.node = spec, node
        self.calls = []
        self.identity = identity
        self.snapshots = snapshots
        self.empty = empty
        doc = {
            "format": spec.snapshot_format,
            "node": snap_node,
            "tables": {"tbl_a": {"columns": ["id"], "rows": [] if empty else [[1], [2]]}},
        }
        self.id_blob = sealed.seal(pub, canonical_bytes({"secret": "s"}))
        self.snap_blob = seal_document(pub, canonical_bytes(doc))

    async def ask(self, action, args):
        self.calls.append((action, args))
        if action == "backup_store_list":
            return {
                "node": args["node"],
                "identity": [v("latest")] if self.identity else [],
                "snapshot": [v(s, empty=self.empty) for s in self.snapshots],
            }
        blob = self.id_blob if args["kind"] == "identity" else self.snap_blob
        return {"kind": args["kind"], "label": args["label"], "meta": {"taken_at": "t"},
                "sealed": base64.b64encode(blob).decode()}


async def test_fetch_bundle_full():
    spec = make_spec()
    k = Keeper(spec)
    b = await restore.fetch_bundle(spec, k.ask, "node-a", k.priv)
    assert b["format"] == "svc/restore/1"
    assert b["identity"] == {"secret": "s"}
    assert b["snapshot"]["node"] == "node-a"
    assert (b["identity_label"], b["snapshot_label"]) == ("latest", "latest")
    assert b["identity_meta"] == b["snapshot_meta"] == {"taken_at": "t"}
    assert [c[1].get("kind") for c in k.calls] == [None, "identity", "snapshot"]


async def test_fetch_bundle_without_snapshot():
    spec = make_spec()
    k = Keeper(spec)
    b = await restore.fetch_bundle(spec, k.ask, "node-a", k.priv, with_snapshot=False)
    assert b["snapshot"] is None and b["snapshot_label"] is None and b["snapshot_meta"] is None
    assert len(k.calls) == 2


async def test_fetch_bundle_no_snapshots_stored():
    spec = make_spec()
    k = Keeper(spec, snapshots=())
    b = await restore.fetch_bundle(spec, k.ask, "node-a", k.priv)
    assert b["snapshot"] is None


async def test_fetch_bundle_missing_identity():
    spec = make_spec()
    k = Keeper(spec, identity=False)
    with pytest.raises(RestoreError, match="нет identity 'latest' для node-a"):
        await restore.fetch_bundle(spec, k.ask, "node-a", k.priv)


async def test_fetch_bundle_wrong_key():
    spec = make_spec()
    k = Keeper(spec)
    other, _ = sealed.generate_keypair()
    with pytest.raises(RestoreError, match="не удалось расшифровать identity"):
        await restore.fetch_bundle(spec, k.ask, "node-a", other)


async def test_fetch_bundle_foreign_snapshot():
    spec = make_spec()
    k = Keeper(spec, snap_node="node-b")
    with pytest.raises(RestoreError, match="принадлежит ноде 'node-b', а не 'node-a'"):
        await restore.fetch_bundle(spec, k.ask, "node-a", k.priv)


async def test_fetch_bundle_wrong_snapshot_format():
    spec = make_spec()
    k = Keeper(make_spec(snapshot_format="other/1"))
    with pytest.raises(RestoreError, match="неизвестный формат снапшота"):
        await restore.fetch_bundle(spec, k.ask, "node-a", k.priv)


async def test_fetch_bundle_broken_reply():
    spec = make_spec()

    async def ask(action, args):
        if action == "backup_store_list":
            return {"identity": [v("latest")]}
        return {"sealed": "***"}

    with pytest.raises(RestoreError, match="ответ хранителя повреждён"):
        await restore.fetch_bundle(spec, ask, "node-a", b"k")


async def test_fetch_bundle_empty_latest_refused():
    spec = make_spec()
    k = Keeper(spec, snapshots=("latest", "last_nonempty"), empty=True)
    with pytest.raises(RestoreError, match="latest пустой"):
        await restore.fetch_bundle(spec, k.ask, "node-a", k.priv)


async def test_summarize_bundle():
    spec = make_spec()
    k = Keeper(spec)
    b = await restore.fetch_bundle(spec, k.ask, "node-a", k.priv)
    b["identity_meta"] = {"updated_at": "u"}
    text = restore.summarize_bundle(spec, b)
    assert text.splitlines() == [
        "Нода: node-a",
        "Identity: latest",
        "  собрана источником: u",
        "  свой: строка",
        "Снапшот БД: latest (снят t), tbl_a 2, активных 1",
        "  по таблицам: {'tbl_a': 2}; не переносятся: ",
    ]


def test_summarize_bundle_no_snapshot_and_empty():
    spec = make_spec()
    base = {"node": "n", "identity_label": "latest", "snapshot": None}
    assert restore.summarize_bundle(spec, base).splitlines()[-1] == (
        "Снапшот БД: нет (будет восстановлена только identity)"
    )
    empty = {**base, "snapshot_label": "latest", "snapshot_meta": None,
             "snapshot": {"tables": {"tbl_a": {"rows": []}, "tbl_b": {"rows": [[1]]}}}}
    lines = restore.summarize_bundle(spec, empty).splitlines()
    assert "  ВНИМАНИЕ: снапшот пустой — данные не вернуть" in lines
    assert lines[-1].endswith("не переносятся: tbl_b")
    assert "снят ?" in "\n".join(lines)
