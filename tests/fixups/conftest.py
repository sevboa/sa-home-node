from __future__ import annotations

import pytest
from world import FakeRoot

from sa_home_node.fixups import registry


@pytest.fixture(autouse=True)
def no_core(monkeypatch) -> None:
    monkeypatch.setattr(registry, "CORE_FIXUPS", ())


@pytest.fixture
def root(monkeypatch):
    return FakeRoot(monkeypatch)
