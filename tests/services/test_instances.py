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


@pytest.fixture
def seeded(tmp_path: Path) -> InstanceStore:
    shutil.copytree(FIXTURES, tmp_path / "instances")
    return InstanceStore(tmp_path / "instances", "node-b")


def put(store: InstanceStore, *names: str) -> None:
    store.dir.mkdir(exist_ok=True)
    store.package_path("svc-i", "one").write_bytes(PACKAGE)
    for name in names:
        (store.dir / name).write_bytes(b"a = 1\n")


@pytest.mark.parametrize("case", WINS, ids=lambda c: c["case"])
def test_wins_over(case: dict) -> None:
    other = InstanceMeta.from_dict(case["other"]) if case["other"] else None
    assert InstanceMeta.from_dict(case["meta"]).wins_over(other) is case["wins"]


def test_reads_reference_and_refuses_overwrite(seeded: InstanceStore) -> None:
    assert seeded.read_package("svc-i", "one") == PACKAGE
    assert seeded.read_meta("svc-i", "one") == META
    assert seeded.known() == [META]
    assert content_hash(PACKAGE) == META.hash
    assert not seeded.apply(META, PACKAGE)
    assert seeded.apply(META, PACKAGE, force=True)


def test_apply_writes_reference_bytes_and_rejects_foreign_hash(store: InstanceStore) -> None:
    assert not store.apply(META, PACKAGE + b"x")
    assert store.known() == [] and store.read_package("svc-i", "one") is None
    assert store.apply(META, PACKAGE)
    assert store.package_path("svc-i", "one").read_bytes() == PACKAGE
    assert store.meta_path("svc-i", "one").read_bytes() == META_BYTES
    assert stat.S_IMODE(store.meta_path("svc-i", "one").stat().st_mode) == 0o600


def test_refresh_bumps_rev(seeded: InstanceStore) -> None:
    assert seeded.refresh("svc-i", "one") is None
    edited = PACKAGE + b"# x\n"
    seeded.package_path("svc-i", "one").write_bytes(edited)
    meta = seeded.refresh("svc-i", "one")
    assert (meta.rev, meta.origin_node, meta.hash) == (2, "node-b", content_hash(edited))
    assert seeded.read_meta("svc-i", "one") == meta
    assert seeded.refresh("svc-i", "one") is None


def test_refresh_first_time_and_missing(store: InstanceStore) -> None:
    assert store.refresh("svc-i", "one") is None
    put(store)
    meta = store.refresh("svc-i", "one")
    assert (meta.rev, meta.hash) == (1, META.hash)
    assert json.loads(store.meta_path("svc-i", "one").read_bytes())["rev"] == 1


@pytest.mark.parametrize("junk", ["plain.toml", "svc.-x.toml", "a b.one.toml"])
def test_badly_named_files_are_skipped(store: InstanceStore, caplog: pytest.LogCaptureFixture, junk: str) -> None:
    put(store, junk)
    with caplog.at_level(logging.WARNING):
        assert [m.instance for m in store.refresh_all()] == ["one"]
        assert [m.instance for m in store.known()] == ["one"]
    assert junk in caplog.text


@pytest.mark.parametrize(
    ("service", "instance"),
    [("..", "one"), ("svc", "../x"), ("svc", "a/b"), ("svc", ""), ("a/b", "one"), ("svc", "-x"), ("svc", "a..b")],
)
def test_unsafe_names_rejected(store: InstanceStore, service: str, instance: str) -> None:
    for call in (store.package_path, store.meta_path, lambda s, i: InstanceMeta(s, i, 1, "h", "", "")):
        with pytest.raises(ValueError):
            call(service, instance)


@pytest.mark.parametrize(
    "data",
    [
        {"service": "svc", "instance": "one", "rev": "x"},
        {"service": "svc", "instance": "one", "rev": None},
        {"service": "svc", "instance": "../one"},
        [],
        None,
    ],
)
def test_from_dict_rejects_broken_records(data: object) -> None:
    with pytest.raises(ValueError):
        InstanceMeta.from_dict(data)


def test_satellite_names_allowed_and_corrupt_meta_is_absent(seeded: InstanceStore) -> None:
    assert InstanceMeta("svc", "bot.node-1.extra", 1, "h", "", "").instance == "bot.node-1.extra"
    seeded.meta_path("svc-i", "one").write_bytes(b"{")
    assert seeded.read_meta("svc-i", "one") is None
    assert seeded.known() == []
