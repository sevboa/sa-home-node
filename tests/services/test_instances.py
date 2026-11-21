from __future__ import annotations

import json
import logging
import shutil
import stat
from pathlib import Path

import pytest

from sa_home_node.services.instances import InstanceMeta, InstanceStore, content_hash

FIXTURES = Path(__file__).parents[1] / "fixtures" / "supervisor" / "instances"
PACKAGE = (FIXTURES / "svc-i.one.toml").read_bytes()
META_BYTES = (FIXTURES / "svc-i.one.rev.json").read_bytes()
META = InstanceMeta.from_dict(json.loads(META_BYTES))
WINS = json.loads((FIXTURES / "wins-over.json").read_text(encoding="utf-8"))["wins_over"]


@pytest.fixture
def store(tmp_path: Path) -> InstanceStore:
    return InstanceStore(tmp_path / "instances", "node-b")


def seeded(tmp_path: Path) -> InstanceStore:
    directory = tmp_path / "instances"
    directory.mkdir()
    shutil.copy(FIXTURES / "svc-i.one.toml", directory)
    shutil.copy(FIXTURES / "svc-i.one.rev.json", directory)
    return InstanceStore(directory, "node-b")


@pytest.mark.parametrize("case", WINS, ids=lambda c: c["case"])
def test_wins_over(case: dict) -> None:
    other = InstanceMeta.from_dict(case["other"]) if case["other"] else None
    assert InstanceMeta.from_dict(case["meta"]).wins_over(other) is case["wins"]


def test_reads_reference(tmp_path: Path) -> None:
    store = seeded(tmp_path)
    assert store.read_package("svc-i", "one") == PACKAGE
    assert store.read_meta("svc-i", "one") == META
    assert store.known() == [META]
    assert content_hash(PACKAGE) == META.hash


def test_apply_writes_reference_bytes(store: InstanceStore) -> None:
    assert store.apply(META, PACKAGE)
    assert store.package_path("svc-i", "one").read_bytes() == PACKAGE
    assert store.meta_path("svc-i", "one").read_bytes() == META_BYTES
    assert stat.S_IMODE(store.meta_path("svc-i", "one").stat().st_mode) == 0o600


def test_apply_rejects_foreign_hash(store: InstanceStore) -> None:
    assert not store.apply(META, PACKAGE + b"x")
    assert store.known() == []
    assert store.read_package("svc-i", "one") is None


def test_apply_respects_wins_over_and_force(tmp_path: Path) -> None:
    store = seeded(tmp_path)
    assert not store.apply(META, PACKAGE)
    assert store.apply(META, PACKAGE, force=True)


def test_refresh_bumps_rev(tmp_path: Path) -> None:
    store = seeded(tmp_path)
    assert store.refresh("svc-i", "one") is None
    store.package_path("svc-i", "one").write_bytes(PACKAGE + b"# x\n")
    meta = store.refresh("svc-i", "one")
    assert meta is not None
    assert (meta.rev, meta.origin_node, meta.hash) == (2, "node-b", content_hash(PACKAGE + b"# x\n"))
    assert store.read_meta("svc-i", "one") == meta
    assert store.refresh("svc-i", "one") is None


def test_refresh_first_time_and_missing(store: InstanceStore) -> None:
    assert store.refresh("svc-i", "one") is None
    store.dir.mkdir()
    store.package_path("svc-i", "one").write_bytes(PACKAGE)
    meta = store.refresh("svc-i", "one")
    assert meta is not None and meta.rev == 1 and meta.hash == META.hash
    assert json.loads(store.meta_path("svc-i", "one").read_bytes())["rev"] == 1


def test_refresh_all_skips_badly_named(store: InstanceStore, caplog: pytest.LogCaptureFixture) -> None:
    store.dir.mkdir()
    store.package_path("svc-i", "one").write_bytes(PACKAGE)
    (store.dir / "plain.toml").write_bytes(b"a = 1\n")
    assert [m.instance for m in store.refresh_all()] == ["one"]
    with caplog.at_level(logging.WARNING):
        assert [m.instance for m in store.known()] == ["one"]
    assert "plain.toml" in caplog.text


def test_corrupt_meta_is_absent(tmp_path: Path) -> None:
    store = seeded(tmp_path)
    store.meta_path("svc-i", "one").write_bytes(b"{")
    assert store.read_meta("svc-i", "one") is None
    assert store.known() == []
