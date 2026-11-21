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

REFERENCE = json.loads((Path(__file__).parents[1] / "fixtures/supervisor/assignments.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", REFERENCE["valid"], ids=lambda c: c["text"])
def test_parse_valid_entries(case: dict) -> None:
    got = parse(case["text"])
    assert (got.service, got.instance, got.role, got.priority) == (
        case["service"], case["instance"], case["role"], case["priority"]
    )
    assert (got.key, got.standby) == (case["key"], case["standby"])


@pytest.mark.parametrize("case", REFERENCE["invalid"], ids=lambda c: c["text"])
def test_parse_invalid_entries(case: dict) -> None:
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


def test_helpers() -> None:
    assert parse("svc-i@bot.node-1.extra").instance == "bot.node-1.extra"
    case = REFERENCE["effective"]
    assert effective(case["config"], case["state"]) == case["result"]
    items = ["svc", "svc-s:standby", "", "@x", "svc:prio=x"]
    assert [a.service for a in parse_valid(items)] == ["svc", "svc-s"]
    assert (has_service(items, "svc"), has_service(items, "svc-s"), has_service(items, "other")) == (True, True, False)
    for instance, base in (("one.x", "one"), ("one", "one"), ("other", "other")):
        assert base_instance(instance, satellites=(".x",)) == base
        assert is_satellite(instance, satellites=(".x",)) == (base != instance)
        assert not is_satellite(instance, satellites=())
