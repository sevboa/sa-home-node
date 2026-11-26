from __future__ import annotations

import re
from dataclasses import dataclass

ROLE_ACTIVE = "active"
ROLE_STANDBY = "standby"
ROLES = (ROLE_ACTIVE, ROLE_STANDBY)

_PRIO_PREFIX = "prio="
_SERVICE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_INSTANCE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class AssignmentError(ValueError):
    pass


@dataclass(frozen=True)
class Assignment:
    service: str
    instance: str = ""
    role: str = ROLE_ACTIVE
    priority: int | None = None

    @property
    def key(self) -> str:
        return slot_key(self.service, self.instance)

    @property
    def standby(self) -> bool:
        return self.role == ROLE_STANDBY


def valid_service(name: str) -> bool:
    return _SERVICE_NAME.fullmatch(name) is not None


def valid_instance(name: str) -> bool:
    return _INSTANCE_NAME.fullmatch(name) is not None and ".." not in name


def slot_key(service: str, instance: str) -> str:
    return f"{service}@{instance}" if instance else service


def base_instance(instance: str, satellites: tuple[str, ...] = ()) -> str:
    for suffix in satellites:
        if instance.endswith(suffix):
            return instance[: -len(suffix)]
    return instance


def is_satellite(instance: str, satellites: tuple[str, ...] = ()) -> bool:
    return base_instance(instance, satellites) != instance


def parse(text: str) -> Assignment:
    raw = text.strip()
    if not raw:
        raise AssignmentError("пустое назначение")
    head, *modifiers = raw.split(":")
    service, _, instance = head.partition("@")
    if not service:
        raise AssignmentError(f"нет имени службы: {text!r}")
    if not valid_service(service.strip()):
        raise AssignmentError(f"недопустимое имя службы: {service!r}")
    if instance.strip() and not valid_instance(instance.strip()):
        raise AssignmentError(f"недопустимое имя инстанса: {instance!r}")
    role = ROLE_ACTIVE
    priority: int | None = None
    for mod in modifiers:
        mod = mod.strip()
        if mod in ROLES:
            role = mod
        elif mod.startswith(_PRIO_PREFIX):
            try:
                priority = int(mod[len(_PRIO_PREFIX) :])
            except ValueError as exc:
                raise AssignmentError(f"приоритет не число: {mod!r}") from exc
        else:
            raise AssignmentError(f"непонятная часть назначения: {mod!r} в {text!r}")
    return Assignment(service=service, instance=instance, role=role, priority=priority)


def parse_valid(items: list[str]) -> list[Assignment]:
    parsed = []
    for item in items:
        try:
            parsed.append(parse(item))
        except AssignmentError:
            continue
    return parsed


def has_service(items: list[str], service: str) -> bool:
    return any(a.service == service for a in parse_valid(items))


def key_or_raw(text: str) -> str:
    try:
        return parse(text).key
    except AssignmentError:
        return text


def effective(config: list[str], state: list[str]) -> list[str]:
    return sorted(set(config) | set(state))


def effective_assignments(config: list[str], state: list[str]) -> tuple[Assignment, ...]:
    return tuple(parse_valid(effective(config, state)))
