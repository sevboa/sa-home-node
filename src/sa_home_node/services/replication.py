from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from sa_home_node.plugins import ReplicationHooks
from sa_home_node.proto.wire import (
    ERR_BAD_REQUEST,
    MSG_COMMAND,
    MSG_GET_STATE,
    NODE_SERVICE,
    Address,
    Envelope,
    ProtoError,
    make_request,
)
from sa_home_node.services.assignments import (
    Assignment,
    base_instance,
    is_satellite,
    slot_key,
    valid_instance,
    valid_service,
)
from sa_home_node.services.events import EVENT_INSTANCE_CONFIG_CHANGED, RUNNING, EventEmitter
from sa_home_node.services.instances import InstanceMeta, InstanceStore
from sa_home_node.services.registry import ServiceTable
from sa_home_node.services.supervisor import Supervisor
from sa_home_node.swarm.relay import EventHandler
from sa_home_node.swarm.router import NodeRouter

log = logging.getLogger(__name__)

ACTION_LIST_INSTANCES = "list_instances"
ACTION_GET_INSTANCE_CONFIG = "get_instance_config"

INSTANCES_KEY = "instances"

POLL_INTERVAL_S = 30.0
PEER_TIMEOUT_S = 10.0


class ConfigReplicator:
    def __init__(
        self,
        node_id: str,
        store: InstanceStore,
        supervisor: Supervisor,
        table: ServiceTable,
        *,
        router: NodeRouter | None,
        emit: EventEmitter,
        subscribe: Callable[[EventHandler], None] | None = None,
        hooks: Iterable[ReplicationHooks] = (),
        poll_interval_s: float = POLL_INTERVAL_S,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._node_id = node_id
        self._store = store
        self._supervisor = supervisor
        self._table = table
        self._router = router
        self._emit = emit
        self._subscribe = subscribe
        self._hooks = tuple(hooks)
        self._poll_interval_s = poll_interval_s
        self._sleep = sleep
        self._task: asyncio.Task | None = None

    def _my_instances(self) -> list[Assignment]:
        return [
            slot.assignment for slot in self._supervisor.services.values() if slot.assignment.instance
        ]

    def _satellites(self, service: str) -> tuple[str, ...]:
        spec = self._table.spec(service)
        return spec.satellites if spec is not None else ()

    def _is_passive(self, service: str, instance: str) -> bool:
        return any(h.passive is not None and h.passive(service, instance) for h in self._hooks)

    def _has_passive(self) -> bool:
        return any(h.passive is not None for h in self._hooks)

    def _cares_about(self, service: str, instance: str) -> bool:
        if self._is_passive(service, instance):
            return True
        base = base_instance(instance, self._satellites(service))
        return any(a.service == service and a.instance == base for a in self._my_instances())

    def _wins(self, meta: InstanceMeta, local: InstanceMeta | None) -> bool:
        if self._is_passive(meta.service, meta.instance) and meta.origin_node == meta.instance:
            if local is None:
                return True
            return meta.hash != local.hash and meta.updated_at > local.updated_at
        return meta.wins_over(local)

    def local_revisions(self) -> list[dict]:
        return [meta.to_dict() for meta in self._store.known()]

    def list_instances(self, args: dict[str, Any]) -> dict[str, Any]:
        service = str(args.get("service", ""))
        revisions = [meta for meta in self.local_revisions() if meta["service"] == service]
        return {"service": service, INSTANCES_KEY: revisions}

    def get_instance_config(self, args: dict[str, Any]) -> dict[str, Any]:
        service = str(args.get("service", ""))
        instance = str(args.get("instance", ""))
        if not valid_service(service) or not valid_instance(instance):
            raise ProtoError(ERR_BAD_REQUEST, f"недопустимое имя пакета настроек: {slot_key(service, instance)!r}")
        data = self._store.read_package(service, instance)
        meta = self._store.read_meta(service, instance)
        if data is None or meta is None:
            raise ProtoError(
                ERR_BAD_REQUEST, f"нет пакета настроек {service}@{instance} на ноде {self._node_id}"
            )
        return {"meta": meta.to_dict(), "content": data.decode("utf-8")}

    async def announce_local_changes(self) -> list[InstanceMeta]:
        for hook in self._hooks:
            if hook.before_announce is None:
                continue
            try:
                await hook.before_announce()
            except Exception:
                log.exception("Репликация: ошибка подготовки локальных пакетов")
        changed = self._store.refresh_all()
        for meta in changed:
            await self._emit(EVENT_INSTANCE_CONFIG_CHANGED, meta.to_dict())
            if not is_satellite(meta.instance, self._satellites(meta.service)):
                await self._restart_if_active(meta)
        return changed

    async def on_event(self, env: Envelope) -> None:
        if env.payload.get("event") != EVENT_INSTANCE_CONFIG_CHANGED:
            return
        meta = self._parse_meta(env.payload.get("data", {}), env.src.node if env.src else "?")
        if meta is None:
            return
        if not self._cares_about(meta.service, meta.instance):
            return
        if not self._wins(meta, self._store.read_meta(meta.service, meta.instance)):
            return
        source = meta.origin_node or (env.src.node if env.src else "")
        if not source or source == self._node_id:
            return
        await self._pull(meta, source)

    async def sync_from_peers(self) -> None:
        if self._router is None:
            return
        if not self._my_instances() and not self._has_passive():
            return
        for peer_id, link in list(self._router.peers.items()):
            if not link.alive:
                continue
            state = await self._peer_state(peer_id, link)
            if state is None:
                continue
            raws = state.get(INSTANCES_KEY) or []
            if not isinstance(raws, list):
                log.warning("Репликация: нода %s прислала список пакетов не списком — пропускаю", peer_id)
                continue
            for raw in raws:
                meta = self._parse_meta(raw, peer_id)
                if meta is None:
                    continue
                if not self._cares_about(meta.service, meta.instance):
                    continue
                if self._wins(meta, self._store.read_meta(meta.service, meta.instance)):
                    await self._pull(meta, peer_id)

    @staticmethod
    def _parse_meta(raw: Any, source: str) -> InstanceMeta | None:
        try:
            return InstanceMeta.from_dict(raw)
        except ValueError as exc:
            log.warning("Репликация: нода %s прислала битую запись пакета (%s) — пропускаю", source, exc)
            return None

    async def _peer_state(self, peer_id: str, link) -> dict | None:
        request = make_request(
            MSG_GET_STATE,
            src=Address(node=self._node_id, service=NODE_SERVICE),
            dst=Address(node=peer_id, service=NODE_SERVICE),
            timeout_s=PEER_TIMEOUT_S,
        )
        try:
            response = await asyncio.wait_for(link.forward(request), PEER_TIMEOUT_S)
        except (ProtoError, TimeoutError, ConnectionError, OSError) as exc:
            log.debug("Репликация: нода %s не ответила о своих пакетах (%s)", peer_id, exc)
            return None
        return response.payload if response.ok else None

    async def _pull(self, meta: InstanceMeta, source: str) -> None:
        if self._router is None:
            return
        link = self._router.peers.get(source)
        if link is None or not link.alive:
            return
        key = slot_key(meta.service, meta.instance)
        request = make_request(
            MSG_COMMAND,
            {
                "action": ACTION_GET_INSTANCE_CONFIG,
                "args": {"service": meta.service, "instance": meta.instance},
            },
            src=Address(node=self._node_id, service=NODE_SERVICE),
            dst=Address(node=source, service=NODE_SERVICE),
            timeout_s=PEER_TIMEOUT_S,
        )
        try:
            response = await asyncio.wait_for(link.forward(request), PEER_TIMEOUT_S)
        except (ProtoError, TimeoutError, ConnectionError, OSError) as exc:
            log.warning("Репликация: не удалось забрать пакет %s с ноды %s (%s) — попробую позже", key, source, exc)
            return
        if not response.ok:
            log.warning("Репликация: нода %s отказала в пакете %s (%s)", source, key, response.error_message() or "?")
            return
        content = response.payload.get("content")
        if not isinstance(content, str):
            return
        remote_meta = self._parse_meta(response.payload.get("meta", meta.to_dict()), source)
        if remote_meta is None:
            return
        data = content.encode("utf-8")
        local = self._store.read_meta(remote_meta.service, remote_meta.instance)
        force = self._is_passive(remote_meta.service, remote_meta.instance) and self._wins(remote_meta, local)
        if not self._store.apply(remote_meta, data, force=force):
            return
        for hook in self._hooks:
            if hook.on_applied is None:
                continue
            try:
                hook.on_applied(remote_meta, data)
            except Exception:
                log.exception("Репликация: ошибка обработки принятого пакета")
        await self._restart_if_active(remote_meta)

    async def _restart_if_active(self, meta: InstanceMeta) -> None:
        base = base_instance(meta.instance, self._satellites(meta.service))
        slot = self._supervisor.get(slot_key(meta.service, base))
        if slot is None or slot.assignment.standby or slot.status != RUNNING:
            return
        log.info("Пакет %s обновлён до ревизии %d — перезапускаю службу", slot.name, meta.rev)
        await slot.restart()

    async def tick(self) -> None:
        await self.announce_local_changes()
        await self.sync_from_peers()

    async def start(self) -> None:
        if self._subscribe is not None:
            self._subscribe(self.on_event)
        with contextlib.suppress(Exception):
            await self.tick()
        self._task = asyncio.create_task(self._run(), name="config-replicator")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            await self._sleep(self._poll_interval_s)
            try:
                await self.tick()
            except Exception:
                log.exception("Репликация: ошибка цикла синхронизации")
