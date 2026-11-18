from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

PROTO_VERSION = 0

MSG_AUTH = "auth"
MSG_HELLO = "hello"
MSG_DESCRIBE = "describe"
MSG_GET_STATE = "get_state"
MSG_COMMAND = "command"
MSG_RESPONSE = "response"
MSG_EVENT = "event"

REQUEST_TYPES = frozenset({MSG_AUTH, MSG_HELLO, MSG_DESCRIBE, MSG_GET_STATE, MSG_COMMAND})

ERR_BAD_REQUEST = "bad_request"
ERR_UNSUPPORTED_PROTO = "unsupported_proto"
ERR_UNKNOWN_TYPE = "unknown_type"
ERR_UNKNOWN_ACTION = "unknown_action"
ERR_UNAUTHORIZED = "unauthorized"
ERR_UNKNOWN_DST = "unknown_dst"
ERR_UNAVAILABLE = "unavailable"
ERR_TIMEOUT = "timeout"
ERR_INTERNAL = "internal"
ERR_NEEDS_PRIVILEGE = "needs_privilege"

MAX_MESSAGE_BYTES = 1 * 1024 * 1024

NODE_SERVICE = "node"


class ProtoError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Address:
    node: str | None = None
    service: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"node": self.node, "service": self.service}

    @classmethod
    def from_dict(cls, raw: Any) -> Address | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ProtoError(ERR_BAD_REQUEST, "адрес должен быть объектом")
        node = raw.get("node")
        service = raw.get("service")
        if node is not None and not isinstance(node, str):
            raise ProtoError(ERR_BAD_REQUEST, "node должен быть строкой")
        if service is not None and not isinstance(service, str):
            raise ProtoError(ERR_BAD_REQUEST, "service должен быть строкой")
        return cls(node=node, service=service)


def node_address(node_id: str | None) -> Address | None:
    if not node_id:
        return None
    return Address(node=node_id, service=NODE_SERVICE)


@dataclass(frozen=True)
class Envelope:
    type: str
    id: str
    v: int = PROTO_VERSION
    src: Address | None = None
    dst: Address | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    ok: bool | None = None
    error: dict[str, Any] | None = None
    timeout_s: float | None = None
    hops: int = 0

    def error_code(self) -> str | None:
        return (self.error or {}).get("code")

    def error_message(self) -> str:
        return (self.error or {}).get("message", "")


def new_id() -> str:
    return uuid.uuid4().hex


def make_request(
    type_: str,
    payload: dict[str, Any] | None = None,
    *,
    src: Address | None = None,
    dst: Address | None = None,
    timeout_s: float | None = None,
) -> Envelope:
    return Envelope(
        type=type_, id=new_id(), src=src, dst=dst, payload=payload or {}, timeout_s=timeout_s
    )


def make_response(request: Envelope, payload: dict[str, Any] | None = None) -> Envelope:
    return Envelope(type=MSG_RESPONSE, id=request.id, ok=True, payload=payload or {})


def make_error_response(request_id: str, code: str, message: str) -> Envelope:
    return Envelope(
        type=MSG_RESPONSE,
        id=request_id,
        ok=False,
        error={"code": code, "message": message},
    )


def make_event(
    event_type: str,
    data: dict[str, Any] | None = None,
    *,
    src: Address | None = None,
) -> Envelope:
    return Envelope(
        type=MSG_EVENT,
        id=new_id(),
        src=src,
        payload={"event": event_type, "data": data or {}},
    )


def encode(env: Envelope) -> bytes:
    raw: dict[str, Any] = {"v": env.v, "id": env.id, "type": env.type}
    if env.src is not None:
        raw["src"] = env.src.to_dict()
    if env.dst is not None:
        raw["dst"] = env.dst.to_dict()
    if env.ok is not None:
        raw["ok"] = env.ok
    if env.error is not None:
        raw["error"] = env.error
    if env.payload:
        raw["payload"] = env.payload
    if env.timeout_s is not None:
        raw["timeout_s"] = env.timeout_s
    if env.hops:
        raw["hops"] = env.hops
    return json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def decode(line: bytes) -> Envelope:
    try:
        raw = json.loads(line)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtoError(ERR_BAD_REQUEST, f"невалидный JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProtoError(ERR_BAD_REQUEST, "сообщение должно быть объектом")

    v = raw.get("v")
    if not isinstance(v, int):
        raise ProtoError(ERR_BAD_REQUEST, "нет версии протокола (v)")
    if v != PROTO_VERSION:
        raise ProtoError(
            ERR_UNSUPPORTED_PROTO, f"версия {v} не поддерживается (наша {PROTO_VERSION})"
        )

    msg_id = raw.get("id")
    msg_type = raw.get("type")
    if not isinstance(msg_id, str) or not msg_id:
        raise ProtoError(ERR_BAD_REQUEST, "нет id сообщения")
    if not isinstance(msg_type, str) or not msg_type:
        raise ProtoError(ERR_BAD_REQUEST, "нет типа сообщения")

    payload = raw.get("payload", {})
    if not isinstance(payload, dict):
        raise ProtoError(ERR_BAD_REQUEST, "payload должен быть объектом")
    error = raw.get("error")
    if error is not None and not isinstance(error, dict):
        raise ProtoError(ERR_BAD_REQUEST, "error должен быть объектом")
    ok = raw.get("ok")
    if ok is not None and not isinstance(ok, bool):
        raise ProtoError(ERR_BAD_REQUEST, "ok должен быть булевым")
    timeout_s = raw.get("timeout_s")
    if timeout_s is not None and not isinstance(timeout_s, (int, float)):
        raise ProtoError(ERR_BAD_REQUEST, "timeout_s должен быть числом")
    hops = raw.get("hops", 0)
    if not isinstance(hops, int) or isinstance(hops, bool) or hops < 0:
        raise ProtoError(ERR_BAD_REQUEST, "hops должен быть неотрицательным целым")

    return Envelope(
        type=msg_type,
        id=msg_id,
        v=v,
        src=Address.from_dict(raw.get("src")),
        dst=Address.from_dict(raw.get("dst")),
        payload=payload,
        ok=ok,
        error=error,
        timeout_s=float(timeout_s) if timeout_s is not None else None,
        hops=hops,
    )


def decode_request(line: bytes) -> Envelope:
    env = decode(line)
    if env.type not in REQUEST_TYPES:
        raise ProtoError(ERR_UNKNOWN_TYPE, f"ожидался запрос, пришёл {env.type}")
    return env


class ServiceUnavailableError(RuntimeError):
    pass


def is_service_unavailable(exc: Exception) -> bool:
    if isinstance(exc, ServiceUnavailableError):
        return True
    return isinstance(exc, ProtoError) and exc.code in (ERR_UNAVAILABLE, ERR_UNKNOWN_DST)
