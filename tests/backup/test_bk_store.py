from __future__ import annotations

import json
import stat

import pytest

from sa_home_node.backup import store as st
from sa_home_node.backup.store import BackupStore, backups_dir, snapshot_is_empty
from sa_home_node.services.instances import InstanceMeta

PRIMARY = "tbl_a"


def _meta(rev: int) -> InstanceMeta:
    return InstanceMeta("svc", "node-a", rev, f"sha256:{rev}", "2026-01-01T00:00:00Z", "node-a")


def _snap(rows: int, rev: int = 1) -> dict:
    return {"rev": rev, "rows": {PRIMARY: rows}}


@pytest.fixture
def store(tmp_path):
    return BackupStore(tmp_path / "backups", PRIMARY)


def test_backups_dir(tmp_path):
    assert backups_dir(tmp_path / "data" / "node.sqlite") == tmp_path / "data" / "backups"


@pytest.mark.parametrize(
    ("meta", "empty"),
    [({}, True), ({"rows": {}}, True), ({"rows": {PRIMARY: 0}}, True), ({"rows": {PRIMARY: 3}}, False),
     ({"rows": {"other": 3}}, True)],
)
def test_snapshot_is_empty(meta, empty):
    assert snapshot_is_empty(meta, PRIMARY) is empty


@pytest.mark.parametrize(
    ("node", "dirname"), [("node-a", "node-a"), ("a/b c", "a_b_c"), ("../x", ".._x"), ("", "_")]
)
def test_node_dir(store, node, dirname):
    assert store.node_dir(node) == store.root / dirname


def test_identity_save_load_and_history(store):
    assert store.load_identity("n") is None
    for rev in range(1, 9):
        store.save_identity("n", f"blob{rev}".encode(), _meta(rev))
    latest = store.load_identity("n")
    assert latest.blob == b"blob8"
    assert latest.meta["rev"] == 8
    assert latest.meta["source"] == "n"
    assert len(store.history("n")) == st.HISTORY_KEEP
    blob_path = store.node_dir("n") / st.IDENTITY_FILE
    assert stat.S_IMODE(blob_path.stat().st_mode) == 0o600
    versions = store.identity_versions("n")
    assert versions[0]["label"] == "latest"
    assert len(versions) == 1 + st.HISTORY_KEEP
    label = versions[1]["label"]
    assert store.read_identity("n", label).blob == b"blob7"
    assert store.read_identity("n", "latest").blob == b"blob8"
    assert store.read_identity("n", "nope") is None
    assert store.read_identity("n", "../identity") is None


def test_snapshot_keeps_last_nonempty(store):
    store.save_snapshot("n", b"full", _snap(5, 1), PRIMARY)
    for rev in range(2, 2 + st.SNAPSHOT_HISTORY_KEEP + 3):
        store.save_snapshot("n", f"empty{rev}".encode(), _snap(0, rev), PRIMARY)
    assert store.load_snapshot("n").meta["empty"] is True
    kept = store.load_last_nonempty("n")
    assert kept.blob == b"full"
    assert kept.meta["empty"] is False
    assert len(store.snapshot_history("n")) == st.SNAPSHOT_HISTORY_KEEP
    labels = [v["label"] for v in store.snapshot_versions("n")]
    assert labels[:2] == ["latest", "last_nonempty"]
    assert len(labels) == 2 + st.SNAPSHOT_HISTORY_KEEP
    assert store.read_snapshot("n", "last_nonempty").blob == b"full"
    assert store.read_snapshot("n", "../latest") is None
    history = store.snapshot_versions("n")[2]
    assert history["meta"]["empty"] is True
    assert store.read_snapshot("n", history["label"]).meta["empty"] is True


def test_snapshot_files(store):
    assert store.load_snapshot("n") is None
    assert store.load_last_nonempty("n") is None
    store.save_snapshot("n", b"x", _snap(2), PRIMARY)
    directory = store.snapshots_dir("n")
    assert sorted(p.name for p in directory.iterdir()) == [
        "last_nonempty.meta.json", "last_nonempty.sealed", "latest.meta.json", "latest.sealed",
    ]
    for name in ("latest.sealed", "last_nonempty.sealed"):
        assert stat.S_IMODE((directory / name).stat().st_mode) == 0o600
    meta = json.loads((directory / "latest.meta.json").read_text(encoding="utf-8"))
    assert meta["rows"] == {PRIMARY: 2}
    assert meta["empty"] is False
    assert "stored_at" in meta
    store.save_snapshot("m", b"y", _snap(0), PRIMARY)
    assert not (store.snapshots_dir("m") / st.SNAPSHOT_NONEMPTY_FILE).exists()
