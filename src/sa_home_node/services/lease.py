from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sa_home_node.proto.wire import (
    MSG_GET_STATE,
    NODE_SERVICE,
    Address,
    ProtoError,
    make_request,
)
from sa_home_node.services.assignments import ROLE_ACTIVE, Assignment
from sa_home_node.services.events import (
    EVENT_SINGLETON_ACTIVATED,
    EVENT_SINGLETON_YIELDED,
    RUNNING,
    EventEmitter,
)
from sa_home_node.services.registry import ServiceTable
from sa_home_node.services.supervisor import Supervisor
from sa_home_node.swarm.kind import traits_for
from sa_home_node.swarm.router import NodeRouter

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 10.0
FAILOVER_GRACE_S = 120.0
FENCED_HOLDOFF_S = 300.0
PEER_TIMEOUT_S = 5.0
STARTUP_SETTLE_S = 15.0
SETTLE_STEP_S = 0.5
SUPERIOR_READY_TIMEOUT_S = 45.0
STUCK_WARN_S = 600.0

SINGLETONS_KEY = "singletons"
REASON_SUPERIOR_NOT_READY = "superior_ready_timeout"

Rank = tuple[int, int, str]


def rank(assignment: Assignment, node_kind: str, node_id: str) -> Rank:
    priority = assignment.priority
    if priority is None:
        priority = traits_for(node_kind).lease_priority
    return (int(assignment.role == ROLE_ACTIVE), priority, node_id)


def outranks(a: Rank, b: Rank) -> bool:
    if a[:2] != b[:2]:
        return a[:2] > b[:2]
    return a[2] < b[2]


def record_rank(record: dict) -> Rank:
    raw = record.get("rank") or [0, 0, record.get("node", "")]
    return (int(raw[0]), int(raw[1]), str(raw[2]))


class LeaseManager:
    def __init__(
        self,
        node_id: str,
        node_kind: str,
        supervisor: Supervisor,
        table: ServiceTable,
        *,
        router: NodeRouter | None,
        emit: EventEmitter,
        grace_s: float = FAILOVER_GRACE_S,
        holdoff_s: float = FENCED_HOLDOFF_S,
        poll_interval_s: float = POLL_INTERVAL_S,
        settle_s: float = STARTUP_SETTLE_S,
        ready_timeout_s: float = SUPERIOR_READY_TIMEOUT_S,
        stuck_warn_s: float = STUCK_WARN_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._node_id = node_id
        self._node_kind = node_kind
        self._supervisor = supervisor
        self._table = table
        self._router = router
        self._emit = emit
        self._grace_s = grace_s
        self._holdoff_s = holdoff_s
        self._poll_interval_s = poll_interval_s
        self._settle_s = settle_s
        self._ready_timeout_s = ready_timeout_s
        self._stuck_warn_s = stuck_warn_s
        self._clock = clock
        self._sleep = sleep
        self._claiming: set[str] = set()
        self._silent_since: dict[str, float] = {}
        self._fenced_until: dict[str, float] = {}
        self._ready: dict[str, bool] = {}
        self._yielded_since: dict[str, float] = {}
        self._not_running_since: dict[str, float] = {}
        self._stuck_warned: set[str] = set()
        self._task: asyncio.Task | None = None

    def _slots(self) -> list:
        return [
            slot
            for slot in self._supervisor.services.values()
            if (spec := self._table.spec(slot.assignment.service)) is not None and spec.singleton
        ]

    def _rank(self, slot) -> Rank:
        return rank(slot.assignment, self._node_kind, self._node_id)

    def local_state(self) -> dict[str, dict]:
        now = self._clock()
        return {
            slot.name: {
                "role": slot.assignment.role,
                "running": slot.status == RUNNING,
                "claiming": slot.name in self._claiming,
                "ready": self._ready.get(slot.name, False),
                "rank": list(self._rank(slot)),
                "fenced": self._fenced_until.get(slot.name, 0.0) > now,
            }
            for slot in self._slots()
        }

    def set_ready(self, slot_name: str, ready: bool) -> None:
        self._ready[slot_name] = ready

    def note_fenced(self, slot_name: str) -> None:
        self._fenced_until[slot_name] = self._clock() + self._holdoff_s
        self._claiming.discard(slot_name)
        self._silent_since.pop(slot_name, None)
        log.warning(
            "Аренда %s: службу вытеснил другой экземпляр — молчу %.0f с", slot_name, self._holdoff_s
        )

    async def _peer_singletons(self) -> dict[str, list[dict]]:
        by_slot: dict[str, list[dict]] = {}
        if self._router is None:
            return by_slot
        for peer_id, link in list(self._router.peers.items()):
            if not link.alive:
                continue
            request = make_request(
                MSG_GET_STATE,
                src=Address(node=self._node_id, service=NODE_SERVICE),
                dst=Address(node=peer_id, service=NODE_SERVICE),
                timeout_s=PEER_TIMEOUT_S,
            )
            try:
                response = await asyncio.wait_for(link.forward(request), PEER_TIMEOUT_S)
            except (ProtoError, TimeoutError, ConnectionError, OSError):
                continue
            if not response.ok:
                continue
            for key, entry in (response.payload.get(SINGLETONS_KEY) or {}).items():
                by_slot.setdefault(key, []).append({**entry, "node": peer_id})
        return by_slot

    async def tick(self) -> None:
        slots = self._slots()
        if not slots:
            return
        peers = await self._peer_singletons()
        for slot in slots:
            await self._decide(slot, peers.get(slot.name, []))

    async def _activate(self, slot, **extra: str) -> None:
        await slot.start()
        await self._emit(
            EVENT_SINGLETON_ACTIVATED, {"slot": slot.name, "node": self._node_id, **extra}
        )

    async def _decide(self, slot, peers: list[dict]) -> None:
        now = self._clock()
        name = slot.name
        mine = self._rank(slot)
        superiors = [p for p in peers if outranks(record_rank(p), mine)]
        inferiors = [p for p in peers if outranks(mine, record_rank(p))]
        holding = [p for p in superiors if p.get("running") or p.get("claiming")]

        if slot.status == RUNNING:
            self._claiming.discard(name)
            self._not_running_since.pop(name, None)
            self._stuck_warned.discard(name)
            if not holding:
                return
            if not slot.process_alive:
                log.warning("Аренда %s: старшая нода вернулась, но процесс уже упал сам", name)
                return
            log.info("Аренда %s: старшая нода вернулась — уступаю", name)
            await slot.stop()
            await self._emit(EVENT_SINGLETON_YIELDED, {"slot": name, "node": self._node_id})
            self._yielded_since[name] = now
            return

        if self._fenced_until.get(name, 0.0) > now:
            return

        stuck_for = now - self._not_running_since.setdefault(name, now)
        if stuck_for >= self._stuck_warn_s and name not in self._stuck_warned:
            self._stuck_warned.add(name)
            log.warning(
                "Аренда %s: слот не работает уже %.0f с (superior_holds=%s, claiming=%s, ready=%s)",
                name,
                stuck_for,
                bool(holding),
                name in self._claiming,
                self._ready.get(name, False),
            )

        if holding:
            self._claiming.discard(name)
            self._silent_since.pop(name, None)
            since = self._yielded_since.get(name)
            expired = since is not None and now - since >= self._ready_timeout_s
            if expired and not any(p.get("ready") for p in holding):
                log.warning(
                    "Аренда %s: старшая нода не подтвердила готовность за %.0f с — забираю",
                    name,
                    self._ready_timeout_s,
                )
                self._yielded_since.pop(name, None)
                await self._activate(slot, reason=REASON_SUPERIOR_NOT_READY)
            return

        self._yielded_since.pop(name, None)

        running_inferior = [p for p in inferiors if p.get("running")]
        if running_inferior:
            if name not in self._claiming:
                log.info(
                    "Аренда %s: службу держит %s — объявляю притязание",
                    name,
                    running_inferior[0].get("node"),
                )
            self._claiming.add(name)
            self._silent_since.pop(name, None)
            return

        if superiors:
            self._silent_since.pop(name, None)
            return

        grace = 0.0 if slot.assignment.role == ROLE_ACTIVE else self._grace_s
        since = self._silent_since.setdefault(name, now)
        if now - since < grace:
            return
        log.info("Аренда %s: свободна — беру", name)
        self._claiming.discard(name)
        self._silent_since.pop(name, None)
        await self._activate(slot)

    async def _settle(self) -> None:
        if self._router is None or not self._router.peers:
            return
        deadline = self._clock() + self._settle_s
        while self._clock() < deadline:
            if all(link.alive or link.down_since is not None for link in self._router.peers.values()):
                return
            await self._sleep(SETTLE_STEP_S)
        log.info("Аренда: не все соседи ответили за %.0f с — решаю по тому, что есть", self._settle_s)

    async def start(self) -> None:
        await self._settle()
        with contextlib.suppress(Exception):
            await self.tick()
        self._task = asyncio.create_task(self._run(), name="lease-manager")

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
                log.exception("Аренда: ошибка цикла")
