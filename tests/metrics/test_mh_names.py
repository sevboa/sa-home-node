from __future__ import annotations

import json
from pathlib import Path

import pytest

from sa_home_node import promexport
from sa_home_node.metrics import names

FIXTURES = Path(__file__).parent.parent / "fixtures" / "metrics"
GOLDEN = json.loads((FIXTURES / "names.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", list(GOLDEN["own"]))
def test_own_names(name):
    assert getattr(names, name) == GOLDEN["own"][name]


@pytest.mark.parametrize("name", list(GOLDEN["shared"]))
def test_shared_names_stay_in_promexport(name):
    assert getattr(promexport, name) == GOLDEN["shared"][name]
    assert not hasattr(names, name)


def test_no_vpn_names():
    public = [item for item in vars(names) if not item.startswith("_")]
    assert not [item for item in public if "VPN" in item.upper()]
    values = [value for value in vars(names).values() if isinstance(value, str)]
    assert not [value for value in values if value.startswith("sa_vpn")]
