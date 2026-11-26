from __future__ import annotations

import pytest
from botfix import load
from drivers import parse_all

from sa_home_node.bot import monitor_parse

EXPECTED = load("render/parsed.json")
PARSED = parse_all(monitor_parse, load("render/parse-inputs.json"))


@pytest.mark.parametrize(
    ("section", "name"),
    [(section, name) for section, names in EXPECTED.items() for name in names],
)
def test_parsing_matches_prototype(section, name):
    assert PARSED[section][name] == EXPECTED[section][name]
