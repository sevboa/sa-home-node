from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from sa_home_node.config import NodeConfig
from sa_home_node.plugins import ServiceSpec
from sa_home_node.proto.describe import ActionParam, ActionSpec
from sa_home_node.proto.wire import ERR_BAD_REQUEST, ProtoError
from sa_home_node.services.assignments import key_or_raw, parse
from sa_home_node.services.inproc import InProcessService, InProcessTarget
from sa_home_node.services.lease import LeaseManager
from sa_home_node.services.registry import ServiceTable
from sa_home_node.services.replication import (
    ACTION_GET_INSTANCE_CONFIG,
    ACTION_LIST_INSTANCES,
    ConfigReplicator,
)
from sa_home_node.services.supervisor import Slot, Supervisor
from sa_home_node.swarm.router import LocalTarget, NodeRouter
from sa_home_node.swarm.service import NodeService
from sa_home_node.swarm.state import NodeState

ACTION_START = "start"
ACTION_STOP = "stop"
ACTION_RESTART = "restart"
ACTION_ASSIGN = "assign"
ACTION_UNASSIGN = "unassign"
ACTION_REPORT_READY = "report_ready"

CONTROL_TITLES = {
    ACTION_START: "▶️ Запустить",
    ACTION_STOP: "⏹ Остановить",
    ACTION_RESTART: "🔄 Перезапустить",
}
NO_CATALOG = "на этой ноде нет каталога пакетов настроек"

LocalLinkFactory = Callable[[str, str], LocalTarget]


class SupervisorActions:
    def __init__(
        self,
        supervisor: Supervisor,
        table: ServiceTable,
        *,
        router: NodeRouter,
        state: NodeState,
        state_path: str | Path,
        make_local_link: LocalLinkFactory,
        lease: LeaseManager | None,
        replicator: ConfigReplicator | None,
        config: NodeConfig,
    ) -> None:
        self._supervisor = supervisor
        self._table = table
        self._router = router
        self._state = state
        self._state_path = state_path
        self._make_local_link = make_local_link
        self._lease = lease
        self._replicator = replicator
        self._config = config

    def register(self, node_service: NodeService) -> None:
        actions = node_service.actions
        for action_id, title in CONTROL_TITLES.items():
            actions.add(self._named(action_id, title, "Служба", self._slot_names), self._control(action_id))
        actions.add(self._named(ACTION_ASSIGN, "➕ Назначить", "Назначение", self._table.assignable_names), self._assign)
        actions.add(self._named(ACTION_UNASSIGN, "➖ Снять", "Служба", self._slot_names), self._unassign)
        actions.add(
            ActionSpec(
                id=ACTION_REPORT_READY,
                title="📶 Готовность службы",
                params=(
                    ActionParam(name="name", required=True, title="Слот службы"),
                    ActionParam(name="ready", type="bool", required=True, title="Готова"),
                ),
            ),
            self._report_ready,
        )
        self._register_instances(node_service)
        node_service.state_parts["services"] = self._services_state
        node_service.state_parts["instances"] = self._instances_state
        node_service.state_parts["singletons"] = self._singletons_state

    def _register_instances(self, node_service: NodeService) -> None:
        actions = node_service.actions
        if self._replicator is None:
            actions.add_hidden(ACTION_LIST_INSTANCES, self._no_catalog)
            actions.add_hidden(ACTION_GET_INSTANCE_CONFIG, self._no_catalog)
            return
        replicator = self._replicator

        async def list_instances(args: dict[str, Any]) -> dict[str, Any]:
            return replicator.list_instances(args)

        async def get_instance_config(args: dict[str, Any]) -> dict[str, Any]:
            return replicator.get_instance_config(args)

        actions.add(
            ActionSpec(
                id=ACTION_LIST_INSTANCES,
                title="📦 Пакеты настроек",
                params=(ActionParam(name="service", required=True, title="Служба"),),
            ),
            list_instances,
        )
        actions.add(
            ActionSpec(
                id=ACTION_GET_INSTANCE_CONFIG,
                title="📦 Забрать пакет настроек",
                params=(
                    ActionParam(name="service", required=True, title="Служба"),
                    ActionParam(name="instance", required=True, title="Инстанс"),
                ),
            ),
            get_instance_config,
        )

    def _slot_names(self) -> tuple[str, ...]:
        return tuple(self._supervisor.services)

    @staticmethod
    def _named(action_id: str, title: str, param_title: str, choices: Callable[[], tuple[str, ...]]):
        def spec() -> ActionSpec:
            param = ActionParam(name="name", required=True, title=param_title, choices=choices())
            return ActionSpec(id=action_id, title=title, params=(param,))

        return spec

    async def _no_catalog(self, args: dict[str, Any]) -> dict[str, Any]:
        raise ProtoError(ERR_BAD_REQUEST, NO_CATALOG)

    def _no_such_service(self, name: str) -> ProtoError:
        known = ", ".join(self._supervisor.services) or "нет служб"
        return ProtoError(ERR_BAD_REQUEST, f"нет такой службы: {name!r} (есть: {known})")

    def _slot(self, name: str) -> Slot:
        slot = self._supervisor.get(name)
        if slot is None:
            raise self._no_such_service(name)
        return slot

    def _control(self, action_id: str):
        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            slot = self._slot(str(args.get("name", "")))
            await getattr(slot, action_id)()
            return {"service": slot.to_dict()}

        return handler

    def _target(self, slot: Slot, spec: ServiceSpec) -> LocalTarget | None:
        if spec.process:
            endpoint = self._table.endpoints(self._config).get(spec.name)
            return self._make_local_link(spec.name, endpoint) if endpoint is not None else None
        if spec.handler is not None and isinstance(slot, InProcessService):
            return InProcessTarget(slot, spec.handler(slot.ctx))
        return None

    async def _assign(self, args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name", ""))
        try:
            assignment = parse(name)
            slot = self._supervisor.assign(name)
        except ValueError as exc:
            raise ProtoError(ERR_BAD_REQUEST, str(exc)) from exc
        spec = self._table.spec(assignment.service)
        if not spec.singleton:
            await slot.start()
        if spec.name not in self._router.local_services:
            target = self._target(slot, spec)
            if target is not None:
                await self._router.add_local_service(spec.name, target)
        if name not in self._state.assignments:
            self._state.assignments.append(name)
            self._state.save(self._state_path)
        return {"service": slot.to_dict()}

    async def _unassign(self, args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name", ""))
        key = key_or_raw(name)
        service = self._slot(key).assignment.service
        await self._supervisor.unassign(key)
        if not any(s.assignment.service == service for s in self._supervisor.services.values()):
            await self._router.remove_local_service(service)
        kept = [item for item in self._state.assignments if key_or_raw(item) != key]
        if len(kept) != len(self._state.assignments):
            self._state.assignments = kept
            self._state.save(self._state_path)
        return {"unassigned": name}

    async def _report_ready(self, args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name", ""))
        self._slot(name)
        ready = bool(args.get("ready", True))
        if self._lease is not None:
            self._lease.set_ready(name, ready)
        return {"name": name, "ready": ready}

    def _services_state(self) -> list[dict[str, Any]]:
        services = [slot.to_dict() for slot in self._supervisor.services.values()]
        supervised = {slot.assignment.service for slot in self._supervisor.services.values()}
        for name, link in self._router.local_services.items():
            if name in supervised:
                continue
            services.append(
                {
                    "name": name,
                    "status": "running" if link.alive else "stopped",
                    "pid": None,
                    "restarts": 0,
                    "last_exit_code": None,
                    "started_at": None,
                    "service": name,
                    "instance": None,
                    "role": None,
                    "external": True,
                }
            )
        return services

    def _instances_state(self) -> list[dict]:
        return self._replicator.local_revisions() if self._replicator is not None else []

    def _singletons_state(self) -> dict[str, dict]:
        return self._lease.local_state() if self._lease is not None else {}


def register_supervisor(
    node_service: NodeService,
    supervisor: Supervisor,
    table: ServiceTable,
    *,
    router: NodeRouter,
    state: NodeState,
    state_path: str | Path,
    make_local_link: LocalLinkFactory,
    lease: LeaseManager | None,
    replicator: ConfigReplicator | None,
    config: NodeConfig,
) -> None:
    SupervisorActions(
        supervisor,
        table,
        router=router,
        state=state,
        state_path=state_path,
        make_local_link=make_local_link,
        lease=lease,
        replicator=replicator,
        config=config,
    ).register(node_service)
