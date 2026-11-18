from dataclasses import replace
from pathlib import Path

import pytest

from sa_home_node.proto.wire import (
    ERR_BAD_REQUEST,
    ERR_UNAVAILABLE,
    ERR_UNKNOWN_DST,
    ERR_UNKNOWN_TYPE,
    ERR_UNSUPPORTED_PROTO,
    MSG_AUTH,
    MSG_COMMAND,
    MSG_GET_STATE,
    MSG_HELLO,
    NODE_SERVICE,
    Address,
    Envelope,
    ProtoError,
    ServiceUnavailableError,
    decode,
    decode_request,
    encode,
    is_service_unavailable,
    make_error_response,
    make_event,
    make_request,
    make_response,
    node_address,
)

LINES = (Path(__file__).parent.parent / "fixtures" / "wire" / "envelopes.ndjson").read_bytes()
FRAMES = [line + b"\n" for line in LINES.splitlines()]
A = Address(node="node-a", service=NODE_SERVICE)


def fid(n: int) -> str:
    return f"{n:032d}"


@pytest.mark.parametrize("line", FRAMES)
def test_roundtrip(line: bytes) -> None:
    assert encode(decode(line)) == line


@pytest.mark.parametrize(
    ("index", "env"),
    [
        (0, replace(make_request(MSG_HELLO), id=fid(1))),
        (
            1,
            replace(
                make_request(
                    MSG_AUTH,
                    {"token": "test-token", "node": "node-a", "incarnation": fid(63)},
                    src=Address(node="node-a"),
                ),
                id=fid(2),
            ),
        ),
        (
            2,
            replace(
                make_request(
                    MSG_COMMAND,
                    {"action": "ping", "args": {"n": 1, "text": "проверка"}},
                    src=A,
                    dst=Address(node="node-b", service="svc"),
                    timeout_s=2.5,
                ),
                id=fid(3),
            ),
        ),
        (
            3,
            replace(
                make_request(MSG_GET_STATE, dst=Address(service="svc"), timeout_s=10.0), id=fid(4)
            ),
        ),
        (
            4,
            make_response(
                Envelope(type=MSG_COMMAND, id=fid(3)),
                {"text": "Привет, рой", "n": 2, "items": [1, 2.5, None, True]},
            ),
        ),
        (5, make_response(Envelope(type=MSG_HELLO, id=fid(1)))),
        (6, make_error_response(fid(5), ERR_UNKNOWN_DST, "нет такой службы: svc")),
        (
            7,
            replace(
                make_event(
                    "node_joined",
                    {
                        "node_id": "node-c",
                        "endpoint": "tcp://192.0.2.30:8710",
                        "endpoints": ["tcp://192.0.2.30:8710"],
                    },
                    src=A,
                ),
                id=fid(6),
                hops=3,
            ),
        ),
        (8, replace(make_event("node_up"), id=fid(7))),
    ],
)
def test_built_from_objects(index: int, env: Envelope) -> None:
    assert encode(env) == FRAMES[index]


@pytest.mark.parametrize(
    ("line", "code"),
    [
        (b'{"v":1,"id":"1","type":"hello"}', ERR_UNSUPPORTED_PROTO),
        (b'{"id":"1","type":"hello"}', ERR_BAD_REQUEST),
        (b"not json", ERR_BAD_REQUEST),
        (b'["x"]', ERR_BAD_REQUEST),
        (b'{"v":0,"type":"hello"}', ERR_BAD_REQUEST),
        (b'{"v":0,"id":"1"}', ERR_BAD_REQUEST),
        (b'{"v":0,"id":"1","type":"hello","payload":"x"}', ERR_BAD_REQUEST),
        (b'{"v":0,"id":"1","type":"hello","error":"x"}', ERR_BAD_REQUEST),
        (b'{"v":0,"id":"1","type":"hello","ok":"x"}', ERR_BAD_REQUEST),
        (b'{"v":0,"id":"1","type":"hello","timeout_s":"1"}', ERR_BAD_REQUEST),
        (b'{"v":0,"id":"1","type":"hello","hops":-1}', ERR_BAD_REQUEST),
        (b'{"v":0,"id":"1","type":"hello","hops":true}', ERR_BAD_REQUEST),
        (b'{"v":0,"id":"1","type":"hello","src":{"node":1}}', ERR_BAD_REQUEST),
    ],
)
def test_bad_frames(line: bytes, code: str) -> None:
    with pytest.raises(ProtoError) as exc:
        decode(line)
    assert exc.value.code == code


def test_decode_request_rejects_response() -> None:
    with pytest.raises(ProtoError) as exc:
        decode_request(FRAMES[5])
    assert exc.value.code == ERR_UNKNOWN_TYPE


def test_compact_frame() -> None:
    frame = encode(Envelope(type=MSG_HELLO, id="x", payload={"text": "Привет", "a": [1, 2]}))
    assert frame == '{"v":0,"id":"x","type":"hello","payload":{"text":"Привет","a":[1,2]}}\n'.encode()


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ServiceUnavailableError("x"), True),
        (ProtoError(ERR_UNAVAILABLE, "x"), True),
        (ProtoError(ERR_UNKNOWN_DST, "x"), True),
        (ProtoError(ERR_BAD_REQUEST, "x"), False),
        (RuntimeError("x"), False),
    ],
)
def test_is_service_unavailable(exc: Exception, expected: bool) -> None:
    assert is_service_unavailable(exc) is expected


@pytest.mark.parametrize(
    ("node_id", "expected"), [("node-a", A), ("", None), (None, None)]
)
def test_node_address(node_id: str | None, expected: Address | None) -> None:
    assert node_address(node_id) == expected


def test_error_helpers() -> None:
    env = decode(FRAMES[6])
    assert (env.error_code(), env.error_message()) == (ERR_UNKNOWN_DST, "нет такой службы: svc")
    assert (decode(FRAMES[5]).error_code(), decode(FRAMES[5]).error_message()) == (None, "")
