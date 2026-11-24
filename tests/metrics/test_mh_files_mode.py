from __future__ import annotations

import stat
from pathlib import Path

import pytest

from sa_home_node.files import atomic_write_bytes, atomic_write_text


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.parametrize(("writer", "data"), [(atomic_write_text, "x"), (atomic_write_bytes, b"x")])
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o600])
def test_mode_applied(tmp_path: Path, writer, data, mode: int) -> None:
    path = tmp_path / "d" / "f"
    writer(path, data, mode=mode)
    assert _mode(path) == mode
    assert sorted(p.name for p in path.parent.iterdir()) == ["f"]


@pytest.mark.parametrize(("writer", "data"), [(atomic_write_text, "x"), (atomic_write_bytes, b"x")])
def test_default_stays_private(tmp_path: Path, writer, data) -> None:
    path = tmp_path / "f"
    writer(path, data)
    assert _mode(path) & 0o077 == 0


def test_mode_on_replace(tmp_path: Path) -> None:
    path = tmp_path / "f"
    atomic_write_text(path, "a")
    atomic_write_text(path, "b", mode=0o644)
    assert path.read_text() == "b"
    assert _mode(path) == 0o644
