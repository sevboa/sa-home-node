from __future__ import annotations

import json
from pathlib import Path

import pytest

from sa_home_node.services.assignments import (
    AssignmentError,
    base_instance,
    effective,
    has_service,
    is_satellite,
    key_or_raw,
    parse,
    parse_valid,
)

REFERENCE_PATH = Path(__file__).parents[1] / "fixtures" / "supervisor" / "assignments.json"
REFERENCE = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
VALID = REFERENCE["valid"]
INVALID = REFERENCE["invalid"]


def test_parse_valid_entries() -> None:
    for case in VALID:
        got = parse(case["text"])
        assert (got.service, got.instance, got.role, got.priority) == (
            case["service"],
            case["instance"],
            case["role"],
            case["priority"],
        )
        assert got.key == case["key"]
        assert got.standby is case["standby"]


def test_parse_invalid_entries() -> None:
    for case in INVALID:
        with pytest.raises(AssignmentError) as exc:
            parse(case["text"])
        assert str(exc.value) == case["error"]
        assert key_or_raw(case["text"]) == case["key_or_raw"]


@pytest.mark.parametrize(
    "text",
    ["../x", "a/b", "-svc", "svc@../x", "svc@a/b", "svc@..", "svc@a..b", "svc@-x", "svc@.x", "svc@a b", "s.v@one", "svc@one\n2"],
)
def test_parse_rejects_unsafe_names(text: str) -> None:
    with pytest.raises(AssignmentError):
        parse(text)
    assert key_or_raw(text) == text


def test_parse_keeps_dots_in_instance() -> None:
    assert parse("svc-i@bot.node-1.extra").instance == "bot.node-1.extra"


def test_effective() -> None:
    case = REFERENCE["effective"]
    assert effective(case["config"], case["state"]) == case["result"]


def test_parse_valid_and_has_service() -> None:
    items = ["svc", "svc-s:standby", "", "@x", "svc:prio=x"]
    parsed = parse_valid(items)
    assert [a.service for a in parsed] == ["svc", "svc-s"]
    assert has_service(items, "svc")
    assert has_service(items, "svc-s")
    assert not has_service(items, "other")


@pytest.mark.parametrize(
    ("instance", "expected"),
    [
        ("one.x", "one"),
        ("one", "one"),
        ("other", "other"),
    ],
)
def test_base_instance_and_satellite(instance: str, expected: str) -> None:
    assert base_instance(instance, satellites=(".x",)) == expected
    assert is_satellite(instance, satellites=(".x",)) == (expected != instance)
    assert not is_satellite(instance, satellites=())
