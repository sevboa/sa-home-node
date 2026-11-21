from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

RUNNING = "running"
RESTARTING = "restarting"
STOPPED = "stopped"

EVENT_SERVICE_STARTED = "service_started"
EVENT_SERVICE_FAILED = "service_failed"
EVENT_SERVICE_STOPPED = "service_stopped"
EVENT_SERVICE_FENCED = "service_fenced"
EVENT_SINGLETON_ACTIVATED = "singleton_activated"
EVENT_SINGLETON_YIELDED = "singleton_yielded"
EVENT_INSTANCE_CONFIG_CHANGED = "instance_config_changed"
EVENT_UPDATE_FINISHED = "update_finished"

FENCED_EXIT_CODE = 11

EventEmitter = Callable[[str, dict], Awaitable[None]]


async def emit_quietly(emit: EventEmitter, event: str, payload: dict) -> None:
    try:
        await emit(event, payload)
    except Exception:
        log.warning("Событие %s не доставлено", event, exc_info=True)


def call_quietly(callback: Callable[[str], None] | None, name: str) -> None:
    if callback is None:
        return
    try:
        callback(name)
    except Exception:
        log.exception("Служба %s: ошибка обратного вызова", name)


def slot_dict(service) -> dict:
    assignment = service.assignment
    fields = ("name", "status", "pid", "restarts", "last_exit_code", "started_at")
    return {
        **{field: getattr(service, field) for field in fields},
        "service": assignment.service,
        "instance": assignment.instance,
        "role": assignment.role,
    }
