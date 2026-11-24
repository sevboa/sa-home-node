from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from sa_home_node.backup import bundle as bundle_mod
from sa_home_node.backup.bundle import RestoreError

FORMAT = "svc/restore/1"
FIXTURES = Path(__file__).parents[1] / "fixtures" / "backup" / "bundle"
CASES = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))


def make(snapshot=True):
    return {
        "format": FORMAT,
        "node": "node-a",
        "identity_label": "latest",
        "snapshot_label": "latest" if snapshot else None,
        "identity_meta": {"updated_at": "t"},
        "snapshot_meta": {"taken_at": "t"} if snapshot else None,
        "identity": {"k": "ключ"},
        "snapshot": {"tables": {"tbl_a": {"columns": ["id"], "rows": [[1]]}}} if snapshot else None,
    }


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_write_dir_tree_and_modes(tmp_path):
    target = tmp_path / "out" / "b"
    written = bundle_mod.write_dir(target, make(), "итог")
    assert [p.name for p in written] == CASES["written_order"]
    assert (target / "summary.txt").read_text("utf-8") == "итог\n"
    assert oct(mode(target)) == CASES["dir_mode"]
    assert {p.name: oct(mode(p)) for p in written} == CASES["file_modes"]
    assert "ключ" in (target / "identity.json").read_text("utf-8")
    assert bundle_mod.load_bundle(target, FORMAT) == make()


def test_write_dir_without_snapshot(tmp_path):
    written = bundle_mod.write_dir(tmp_path / "b", make(snapshot=False), "s")
    assert sorted(p.name for p in written) == CASES["no_snapshot_written"]
    assert bundle_mod.load_bundle(tmp_path / "b", FORMAT)["snapshot"] is None


def test_write_dir_accepts_empty_existing(tmp_path):
    (tmp_path / "b").mkdir()
    bundle_mod.write_dir(tmp_path / "b", make(), "s")
    assert oct(mode(tmp_path / "b")) == CASES["write_dir_existing_empty_mode"]


@pytest.mark.parametrize("name", ["full", "no-snapshot"])
def test_write_dir_matches_reference(tmp_path, name):
    source = FIXTURES / name
    bundle = bundle_mod.load_bundle(source, "sa-home-bot/svc-restore/1")
    bundle_mod.write_dir(tmp_path / "out", bundle, (source / "summary.txt").read_text("utf-8").rstrip("\n"))
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(p.name for p in source.iterdir())
    for path in source.iterdir():
        assert (tmp_path / "out" / path.name).read_bytes() == path.read_bytes()


def test_write_dir_refuses_non_empty(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "x").write_text("1")
    with pytest.raises(RestoreError, match="не пуст"):
        bundle_mod.write_dir(tmp_path / "b", make(), "s")


def test_bytes_roundtrip_via_file(tmp_path):
    path = tmp_path / "restore-bundle.json"
    path.write_bytes(bundle_mod.bundle_to_bytes(make()))
    assert bundle_mod.load_bundle(path, FORMAT) == make()


def test_load_errors(tmp_path):
    missing = tmp_path / "none.json"
    with pytest.raises(RestoreError, match="не удалось прочитать"):
        bundle_mod.load_bundle(missing, FORMAT)
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    with pytest.raises(RestoreError, match="не удалось прочитать"):
        bundle_mod.load_bundle(bad, FORMAT)
    (tmp_path / "d").mkdir()
    with pytest.raises(RestoreError, match="не удалось прочитать"):
        bundle_mod.load_bundle(tmp_path / "d", FORMAT)
    other = tmp_path / "other.json"
    other.write_bytes(bundle_mod.bundle_to_bytes({**make(), "format": "x"}))
    with pytest.raises(RestoreError, match="неизвестный формат бандла: 'x'"):
        bundle_mod.load_bundle(other, FORMAT)


class Run:
    def __init__(self, code=0, err=b""):
        self.calls = []
        self.code, self.err = code, err

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, self.code, b"", self.err)


def test_stage_over_ssh(monkeypatch):
    run = Run()
    monkeypatch.setattr(bundle_mod.subprocess, "run", run)
    bundle_mod.stage_over_ssh("h", b"data")
    argv, kw = run.calls[0]
    assert argv == ["ssh", "h", "~/.local/bin/nodectl backup stage"]
    assert kw["input"] == b"data"


def test_stage_over_ssh_failure(monkeypatch):
    monkeypatch.setattr(bundle_mod.subprocess, "run", Run(3, "ой".encode()))
    with pytest.raises(RestoreError, match="кодом 3: ой"):
        bundle_mod.stage_over_ssh("h", b"", "/bin/n")


@pytest.mark.parametrize(
    ("extra", "cmd"),
    [("", "/bin/n backup apply --yes"), ("--wipe", "/bin/n backup apply --yes --wipe")],
)
def test_apply_over_ssh(monkeypatch, extra, cmd):
    run = Run(5)
    monkeypatch.setattr(bundle_mod.subprocess, "run", run)
    assert bundle_mod.apply_over_ssh("h", "/bin/n", extra) == 5
    assert run.calls[0][0] == ["ssh", "-t", "h", cmd]
