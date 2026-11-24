from __future__ import annotations

from pathlib import Path

from sa_home_node.files import atomic_write_text


def write_changed(files: dict[Path, str]) -> list[Path]:
    written: list[Path] = []
    for path, content in files.items():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError:
            existing = None
        if existing != content:
            atomic_write_text(path, content, mode=0o644)
            written.append(path)
    return written
