from __future__ import annotations

import socket
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sa_home_node import __version__
from sa_home_node.proto.describe import ActionParam, ActionSpec, ServiceDescription, ServiceInfo
from sa_home_node.proto.wire import ERR_BAD_REQUEST, ERR_UNKNOWN_ACTION, ProtoError
from sa_home_node.tasks import protocol
from sa_home_node.tasks.config import TasksConfig
from sa_home_node.tasks.store import TaskStore

if TYPE_CHECKING:
    from sa_home_node.tasks.loops import TaskRunner


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _object_or_none(args: dict[str, Any], name: str) -> dict[str, Any] | None:
    value = args.get(name)
    if value is not None and not isinstance(value, dict):
        raise ProtoError(ERR_BAD_REQUEST, f"{name} должен быть объектом")
    return value


class TasksService:
    def __init__(
        self,
        config: TasksConfig,
        store: TaskStore,
        runner: TaskRunner,
        *,
        node: str | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._runner = runner
        self._node = node if node is not None else socket.gethostname()

    def describe(self) -> ServiceDescription:
        return ServiceDescription(
            info=ServiceInfo(node=self._node, service=protocol.SERVICE_NAME, version=__version__),
            capabilities=(),
            actions=(
                ActionSpec(
                    id=protocol.ACTION_CREATE,
                    title="Поставить отложенную задачу",
                    params=(
                        ActionParam("due_at", "string", True, "Когда (ISO 8601)"),
                        ActionParam("dst_node", "string", True, "Нода-адресат"),
                        ActionParam("dst_service", "string", True, "Служба-адресат"),
                        ActionParam("action", "string", True, "Действие (или chat_loop — см. protocol.py)"),
                        ActionParam("args", "string", False, "Аргументы действия"),
                        ActionParam("timeout_s", "float", False, "Таймаут исполнения (сек)"),
                        ActionParam("meta", "string", False, "Непрозрачные метаданные заказчика"),
                        ActionParam(
                            "await_event",
                            "string",
                            False,
                            "Ждать событие {node, event_type} раньше due_at",
                        ),
                    ),
                ),
                ActionSpec(
                    id=protocol.ACTION_FIRE_NOW,
                    title="Разбудить задачу немедленно",
                    params=(ActionParam("task_id", "int", True, "Id задачи"),),
                ),
                ActionSpec(
                    id=protocol.ACTION_MATCH_EVENT,
                    title="Сверить событие роя с ожидающими задачами",
                    params=(
                        ActionParam("node", "string", True, "Нода"),
                        ActionParam("event_type", "string", True, "Тип события"),
                    ),
                ),
            ),
        )

    async def get_state(self) -> dict[str, Any]:
        return {"node": self._node, "service": protocol.SERVICE_NAME}

    async def run_command(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        if action == protocol.ACTION_CREATE:
            return await self._create(args)
        if action == protocol.ACTION_FIRE_NOW:
            return await self._fire_now(args)
        if action == protocol.ACTION_MATCH_EVENT:
            return await self._match_event(args)
        raise ProtoError(ERR_UNKNOWN_ACTION, f"нет такого действия: {action}")

    async def _create(self, args: dict[str, Any]) -> dict[str, Any]:
        due_raw = args.get("due_at")
        dst_node = args.get("dst_node")
        dst_service = args.get("dst_service")
        task_action = args.get("action")
        if not all(_non_empty(v) for v in (due_raw, dst_node, dst_service, task_action)):
            raise ProtoError(
                ERR_BAD_REQUEST,
                "due_at/dst_node/dst_service/action обязательны и должны быть непустыми строками",
            )
        try:
            due_at = datetime.fromisoformat(due_raw)
        except ValueError as exc:
            raise ProtoError(ERR_BAD_REQUEST, f"due_at не в формате ISO 8601: {exc}") from exc
        if due_at.tzinfo is None:
            due_at = due_at.astimezone()
        task_args = _object_or_none(args, "args")
        meta = _object_or_none(args, "meta")
        timeout_s = args.get("timeout_s")
        if timeout_s is not None and not isinstance(timeout_s, int | float):
            raise ProtoError(ERR_BAD_REQUEST, "timeout_s должен быть числом")
        await_event = args.get("await_event")
        if await_event is not None and not (
            isinstance(await_event, dict)
            and _non_empty(await_event.get("node"))
            and _non_empty(await_event.get("event_type"))
        ):
            raise ProtoError(ERR_BAD_REQUEST, "await_event должен быть {node: str, event_type: str}")

        now = datetime.now(tz=UTC)
        task_id = await self._store.create_task(
            dst_node,
            dst_service,
            task_action,
            task_args or {},
            float(timeout_s) if timeout_s else self._config.default_timeout_s,
            meta or {},
            due_at.astimezone(UTC),
            now,
        )
        if await_event is not None:
            await self._store.add_event_waiter(task_id, await_event["node"], await_event["event_type"], now)
        return {"task_id": task_id}

    async def _fire_now(self, args: dict[str, Any]) -> dict[str, Any]:
        task_id = args.get("task_id")
        if not isinstance(task_id, int):
            raise ProtoError(ERR_BAD_REQUEST, "task_id обязателен и должен быть числом")
        row = await self._store.get_pending_task(task_id)
        if row is None:
            return {"fired": False, "reason": "нет такой задачи или уже сработала"}
        await self._store.mark_task_fired(row["id"], datetime.now(tz=UTC))
        await self._store.pop_event_waiter(row["id"])
        self._runner.fire(row)
        return {"fired": True}

    async def _match_event(self, args: dict[str, Any]) -> dict[str, Any]:
        node = args.get("node")
        event_type = args.get("event_type")
        if not (_non_empty(node) and _non_empty(event_type)):
            raise ProtoError(ERR_BAD_REQUEST, "node и event_type обязательны")
        task_id = await self._store.pop_event_waiter_for(node, event_type)
        if task_id is None:
            return {"matched": False}
        fired = await self._fire_now({"task_id": task_id})
        return {"matched": bool(fired.get("fired")), "task_id": task_id}
