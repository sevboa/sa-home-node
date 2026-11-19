from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator

import pytest


@pytest.fixture
def sock_dir() -> Iterator[str]:
    path = tempfile.mkdtemp()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
