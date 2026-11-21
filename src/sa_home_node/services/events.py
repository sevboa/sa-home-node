from __future__ import annotations

from collections.abc import Awaitable, Callable

RUNNING = "running"
RESTARTING = "restarting"
STOPPED = "stopped"

EVENT_SERVICE_STARTED = "service_started"
EVENT_SERVICE_FAILED = "service_failed"
EVENT_SERVICE_STOPPED = "service_stopped"
EVENT_SERVICE_FENCED = "service_fenced"

FENCED_EXIT_CODE = 11

EventEmitter = Callable[[str, dict], Awaitable[None]]


def slot_dict(service) -> dict:
    assignment = service.assignment
    return {
        "name": service.name,
        "status": service.status,
        "pid": service.pid,
        "restarts": service.restarts,
        "last_exit_code": service.last_exit_code,
        "started_at": service.started_at,
        "service": assignment.service,
        "instance": assignment.instance,
        "role": assignment.role,
    }
