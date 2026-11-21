from __future__ import annotations

from pathlib import Path

import pytest

from sa_home_node.files import atomic_write_bytes, atomic_write_text


def test_text_and_bytes_roundtrip(tmp_path: Path) -> None:
    atomic_write_text(tmp_path / "a" / "t.txt", "привет")
    atomic_write_bytes(tmp_path / "a" / "b.bin", b"\x00\xff")
    assert (tmp_path / "a" / "t.txt").read_bytes() == "привет".encode()
    assert (tmp_path / "a" / "b.bin").read_bytes() == b"\x00\xff"


def test_replaces_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "f"
    atomic_write_bytes(target, b"1")
    atomic_write_bytes(target, b"2")
    assert target.read_bytes() == b"2"
    assert [p.name for p in tmp_path.iterdir()] == ["f"]


def test_failure_cleans_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(src: str, dst: object) -> None:
        raise OSError("x")

    monkeypatch.setattr("sa_home_node.files.os.replace", boom)
    with pytest.raises(OSError):
        atomic_write_bytes(tmp_path / "f", b"1")
    assert list(tmp_path.iterdir()) == []
