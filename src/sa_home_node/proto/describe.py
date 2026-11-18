from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sa_home_node.proto.wire import ERR_BAD_REQUEST, PROTO_VERSION, ProtoError

_KNOWN_KEYS = frozenset(
    {
        "node",
        "service",
        "version",
        "node_kind",
        "wake",
        "endpoints",
        "proto",
        "links",
        "busy",
        "working",
        "capabilities",
        "actions",
    }
)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_links(raw: Any) -> dict[str, dict[str, Any]] | None:
    if not isinstance(raw, dict):
        return None
    links: dict[str, dict[str, Any]] = {}
    for peer, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        rtt = entry.get("rtt_ms")
        loss = entry.get("loss")
        links[str(peer)] = {
            "rtt_ms": float(rtt) if _is_number(rtt) else None,
            "loss": float(loss) if _is_number(loss) else 0.0,
            "alive": entry.get("alive") is True,
        }
    return links


@dataclass(frozen=True)
class ServiceInfo:
    node: str
    service: str
    version: str
    node_kind: str = ""
    wake: dict[str, str] | None = None
    endpoints: tuple[str, ...] = ()
    links: dict[str, dict[str, Any]] | None = None
    busy: bool = False
    working: bool | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "node": self.node,
            "service": self.service,
            "version": self.version,
            "node_kind": self.node_kind,
            "wake": self.wake,
            "endpoints": list(self.endpoints),
            "proto": PROTO_VERSION,
        }
        if self.links is not None:
            payload["links"] = self.links
        if self.busy:
            payload["busy"] = True
        if self.working is not None:
            payload["working"] = self.working
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ServiceInfo:
        wake = payload.get("wake")
        raw_endpoints = payload.get("endpoints")
        try:
            return cls(
                node=str(payload["node"]),
                service=str(payload["service"]),
                version=str(payload["version"]),
                node_kind=str(payload.get("node_kind", "")),
                wake={str(k): str(v) for k, v in wake.items()} if isinstance(wake, dict) else None,
                endpoints=(
                    tuple(str(e) for e in raw_endpoints if e)
                    if isinstance(raw_endpoints, list)
                    else ()
                ),
                links=_parse_links(payload.get("links")),
                busy=payload.get("busy") is True,
                working=(
                    payload["working"] if isinstance(payload.get("working"), bool) else None
                ),
            )
        except KeyError as exc:
            raise ProtoError(ERR_BAD_REQUEST, f"hello без поля {exc}") from exc


@dataclass(frozen=True)
class ActionParam:
    name: str
    type: str = "string"
    required: bool = True
    title: str | None = None
    choices: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        raw: dict[str, Any] = {"name": self.name, "type": self.type, "required": self.required}
        if self.title is not None:
            raw["title"] = self.title
        if self.choices is not None:
            raw["choices"] = list(self.choices)
        return raw

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ActionParam:
        choices = raw.get("choices")
        return cls(
            name=str(raw["name"]),
            type=str(raw.get("type", "string")),
            required=bool(raw.get("required", True)),
            title=raw.get("title"),
            choices=tuple(str(c) for c in choices) if choices is not None else None,
        )


@dataclass(frozen=True)
class ActionSpec:
    id: str
    title: str
    params: tuple[ActionParam, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "params": [p.to_dict() for p in self.params]}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ActionSpec:
        return cls(
            id=str(raw["id"]),
            title=str(raw.get("title", raw["id"])),
            params=tuple(ActionParam.from_dict(p) for p in raw.get("params", [])),
        )


@dataclass(frozen=True)
class ServiceDescription:
    info: ServiceInfo
    capabilities: tuple[str, ...] = ()
    actions: tuple[ActionSpec, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = self.info.to_payload()
        payload["capabilities"] = list(self.capabilities)
        payload["actions"] = [a.to_dict() for a in self.actions]
        payload.update(self.extra)
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ServiceDescription:
        extra = {k: v for k, v in payload.items() if k not in _KNOWN_KEYS}
        try:
            return cls(
                info=ServiceInfo.from_payload(payload),
                capabilities=tuple(str(c) for c in payload.get("capabilities", [])),
                actions=tuple(ActionSpec.from_dict(a) for a in payload.get("actions", [])),
                extra=extra,
            )
        except (KeyError, TypeError) as exc:
            raise ProtoError(ERR_BAD_REQUEST, f"невалидный describe: {exc}") from exc

    def find_action(self, action_id: str) -> ActionSpec | None:
        for action in self.actions:
            if action.id == action_id:
                return action
        return None
