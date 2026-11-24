from __future__ import annotations

import json
import math
import tomllib
from pathlib import Path

import pytest

from sa_home_node.toml_emit import toml_string, toml_table, toml_tables, toml_value

FIXTURE = Path(__file__).parent / "fixtures" / "backup" / "toml.json"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "true"),
        (False, "false"),
        (7, "7"),
        (-3, "-3"),
        (1.5, "1.5"),
        ("текст", '"текст"'),
        ('a"b', '"a\\"b"'),
        ("a\nb", '"a\\nb"'),
        ([], "[]"),
        ([1, "x", True], '[1, "x", true]'),
        ([[1, 2], ["a"]], '[[1, 2], ["a"]]'),
    ],
)
def test_toml_value(value, expected):
    assert toml_value(value) == expected


def test_toml_string_keeps_unicode():
    assert toml_string("привет") == '"привет"'


def test_list_of_dicts_rejected():
    with pytest.raises(ValueError):
        toml_value([{"a": 1}])
    with pytest.raises(ValueError):
        toml_tables("x", {"items": [1, {"a": 1}]})


def test_toml_table_skips_none():
    assert toml_table("t", {"a": 1, "b": None, "c": "z"}) == '[t]\na = 1\nc = "z"\n'


CASES = [
    {"a": 1, "b": 2.5, "c": True, "d": "s", "e": [1, 2]},
    {"text": "юникод ✓", "quote": 'say "hi"', "nl": "a\nb\tc", "back": "p\\q"},
    {"a": 1, "n": None, "b": "x"},
    {"top": 1, "one": {"x": 1, "two": {"y": "z", "three": {"w": [1, 2]}}}},
    {"sub": {}},
    {},
    {"odd key": 1, "sub": {"a.b": 2, "c d": {"e": 3}}},
    {"s": 1, "t": {"a": 1}, "u": 2, "v": {"b": 2}},
]


@pytest.mark.parametrize("values", CASES)
def test_roundtrip_through_tomllib(values):
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if v is not None}
        return value

    parsed = tomllib.loads(toml_tables("root", values))
    assert parsed == {"root": strip(values)}


def test_nested_header_order_and_blank_lines():
    out = toml_tables("r", {"a": 1, "x": {"b": 2, "y": {"c": 3}}, "z": {"d": 4}})
    assert out == "[r]\na = 1\n\n[r.x]\nb = 2\n\n[r.x.y]\nc = 3\n\n[r.z]\nd = 4\n"


def test_empty_dict_keeps_header():
    assert toml_tables("r", {"sub": {}}) == "[r]\n\n[r.sub]\n"


LOSSY = {"list_of_dict", "list_of_dict_becomes_string", "weird_keys_unquoted", "unicode_nested"}


def _load(section, lossy=False):
    if not FIXTURE.exists():
        return []
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))[section]
    return [c for c in cases if (c.get("name") in LOSSY) == lossy]


def _eval(case, key):
    if key in case:
        return case[key]
    return eval(case[key + "_repr"], {"inf": math.inf, "nan": math.nan})


@pytest.mark.parametrize("case", _load("toml_value"), ids=lambda c: c["name"])
def test_fixture_toml_value(case):
    value = eval(case["repr"], {"inf": math.inf, "nan": math.nan})
    assert toml_value(value) == case["result"]["ok"]


@pytest.mark.parametrize("case", _load("toml_string"))
def test_fixture_toml_string(case):
    assert toml_string(_eval(case, "input")) == case["result"]["ok"]


@pytest.mark.parametrize("case", _load("toml_table"), ids=lambda c: c["name"])
def test_fixture_toml_table(case):
    assert toml_table(case["table"], case["values"]) == case["result"]["ok"]


@pytest.mark.parametrize("case", _load("toml_tables_nested"), ids=lambda c: c["name"])
def test_fixture_toml_tables(case):
    assert toml_tables(case["table"], case["values"]) == case["joined"]


def test_fixture_lossy_cases_rejected_or_quoted():
    for section, call in (
        ("toml_value", lambda c: toml_value(eval(c["repr"]))),
        ("toml_table", lambda c: toml_table(c["table"], c["values"])),
        ("toml_tables_nested", lambda c: toml_tables(c["table"], c["values"])),
    ):
        for case in _load(section, lossy=True):
            if "list_of_dict" in case["name"]:
                with pytest.raises(ValueError):
                    call(case)
            else:
                assert tomllib.loads(call(case)) is not None


def test_non_bare_keys_quoted():
    assert toml_table("svc", {"has space": 1, "ключ": 2, "": 3}) == '[svc]\n"has space" = 1\n"ключ" = 2\n"" = 3\n'
