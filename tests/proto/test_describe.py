from __future__ import annotations

import json
from pathlib import Path

import pytest

from sa_home_node.proto.describe import (
    ActionSpec,
    ServiceDescription,
    ServiceInfo,
)
from sa_home_node.proto.wire import ProtoError

FIXTURES = Path(__file__).parent.parent / "fixtures" / "wire"


def _load_json(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize("raw", _load_json("hello.json"))
def test_service_info_roundtrip(raw: dict) -> None:
    info = ServiceInfo.from_payload(raw)
    rebuilt = info.to_payload()
    assert json.dumps(raw, ensure_ascii=False) == json.dumps(rebuilt, ensure_ascii=False)


def test_service_info_missing_node_raises() -> None:
    raw = {"service": "svc", "version": "1.0.0"}
    with pytest.raises(ProtoError) as exc:
        ServiceInfo.from_payload(raw)
    assert exc.value.code == "bad_request"
    assert "hello без поля" in exc.value.message


@pytest.mark.parametrize(
    ("raw_links", "expected"),
    [
        (None, None),
        ("not a dict", None),
        ({"peer": "not a dict"}, {}),
        (
            {"peer": {"rtt_ms": "bad", "loss": "bad", "alive": "yes"}},
            {"peer": {"rtt_ms": None, "loss": 0.0, "alive": False}},
        ),
        (
            {"peer": {"rtt_ms": 10, "loss": 0.1, "alive": True}},
            {"peer": {"rtt_ms": 10.0, "loss": 0.1, "alive": True}},
        ),
    ],
)
def test_parse_links_normalization(raw_links, expected) -> None:
    raw = {
        "node": "node-a",
        "service": "svc",
        "version": "1.0.0",
        "links": raw_links,
    }
    info = ServiceInfo.from_payload(raw)
    assert info.links == expected


@pytest.mark.parametrize(
    ("busy_val", "expected"),
    [(True, True), (False, False), (None, False), ("true", False), (1, False)],
)
def test_busy_normalization(busy_val, expected) -> None:
    raw = {"node": "node-a", "service": "svc", "version": "1.0.0", "busy": busy_val}
    info = ServiceInfo.from_payload(raw)
    assert info.busy == expected


@pytest.mark.parametrize("raw", _load_json("node-actions.json"))
def test_action_spec_roundtrip(raw: dict) -> None:
    spec = ActionSpec.from_dict(raw)
    rebuilt = spec.to_dict()
    assert json.dumps(raw, ensure_ascii=False) == json.dumps(rebuilt, ensure_ascii=False)


def test_service_description_roundtrip() -> None:
    raw_list = _load_json("describe.json")
    for raw in raw_list:
        desc = ServiceDescription.from_payload(raw)
        rebuilt = desc.to_payload()
        assert json.dumps(raw, ensure_ascii=False) == json.dumps(rebuilt, ensure_ascii=False)
        assert desc.extra == {"model_profile": {"name": "test-profile", "context": 4096}}


def test_service_description_bad_payload_raises() -> None:
    raw = {"node": "node-a", "service": "svc", "version": "1.0.0", "capabilities": 123}
    with pytest.raises(ProtoError) as exc:
        ServiceDescription.from_payload(raw)
    assert exc.value.code == "bad_request"
    assert "невалидный describe" in exc.value.message


def test_find_action() -> None:
    raw_list = _load_json("describe.json")
    desc = ServiceDescription.from_payload(raw_list[0])
    assert desc.find_action("ping") is not None
    assert desc.find_action("set_mode") is not None
    assert desc.find_action("unknown") is None
