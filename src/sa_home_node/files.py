from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def atomic_write_bytes(path: str | Path, data: bytes, mode: int | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        if mode is not None:
            os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def atomic_write_text(path: str | Path, text: str, mode: int | None = None) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), mode=mode)
