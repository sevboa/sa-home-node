from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sa_home_node.proto.wire import Address
from sa_home_node.services.periodic import PEER_ERRORS
from sa_home_node.tasks import protocol
from sa_home_node.tasks.config import TasksConfig
from sa_home_node.tasks.kinds import EventEmitter, TaskCall, TaskKind
from sa_home_node.tasks.protocol import FIRE_GRACE_S, FIRE_RETRY_INTERVAL_S, POLL_INTERVAL_S, PREWAKE_LEAD_S
from sa_home_node.tasks.store import TaskStore
from sa_home_node.wake import peers, ready
from sa_home_node.wake.peers import NodeLink, WakeCache

log = logging.getLogger(__name__)


class TaskRunner:
    def __init__(
        self,
        link: NodeLink,
        store: TaskStore,
        cache: WakeCache,
        kinds: Sequence[TaskKind],
        emit: EventEmitter,
        config: TasksConfig,
        *,
        sleep: Callable[[float], Any] = asyncio.sleep,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._link = link
        self._store = store
        self._cache = cache
        self._kinds = {kind.action: kind for kind in kinds}
        self._emit = emit
        self._config = config
        self._sleep = sleep
        self._clock = clock or (lambda: asyncio.get_running_loop().time())
        self._prewake_inflight: dict[int, asyncio.Task] = {}
        self._waking_announced: set[int] = set()
        self._jobs: set[asyncio.Task] = set()
        self._loops: list[asyncio.Task] = []

    def start(self) -> None:
        self._loops = [
            asyncio.create_task(self.prewake_loop(), name="tasks-prewake-loop"),
            asyncio.create_task(self.fire_loop(), name="tasks-fire-loop"),
        ]

    async def stop(self) -> None:
        pending = [*self._loops, *self._jobs]
        for job in pending:
            job.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self._loops = []

    def _spawn(self, coro: Any, name: str) -> asyncio.Task:
        job = asyncio.create_task(coro, name=name)
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)
        return job

    async def prewake_loop(self) -> None:
        while True:
            await self._sleep(POLL_INTERVAL_S)
            try:
                await self.prewake_due()
            except Exception:
                log.exception("tasks: сбой прогрева")

    async def prewake_due(self) -> None:
        deadline = datetime.now(tz=UTC) + timedelta(seconds=PREWAKE_LEAD_S)
        for row in await self._store.tasks_needing_prewake(deadline):
            task_id = row["id"]
            await self._store.mark_task_prewake_done(task_id)
            kind = self._kinds.get(row["action"])
            if kind is not None and not kind.prewake:
                continue
            job = self._spawn(self._prewake_one_safe(row), f"tasks-prewake-{task_id}")
            self._prewake_inflight[task_id] = job
            job.add_done_callback(lambda _job, tid=task_id: self._prewake_inflight.pop(tid, None))

    async def _prewake_one_safe(self, row: dict) -> None:
        try:
            await self._prewake_one(row)
        except Exception:
            log.exception("tasks: сбой прогрева задачи id=%s", row["id"])

    async def _prewake_one(self, row: dict) -> None:
        task_id = row["id"]
        meta = json.loads(row["meta_json"])
        dst = Address(node=row["dst_node"], service=row["dst_service"])
        state = await peers.fetch_state(self._link, dst, timeout_s=ready.PRESENCE_TIMEOUT_S)
        if state is not None and not state.get("asleep"):
            log.info("tasks: задача id=%s — цель уже тёплая, прогрев не нужен", task_id)
            return
        await self._announce_waking(task_id, meta)
        outcome = await ready.ensure_service_ready(
            self._link,
            self._cache,
            row["dst_node"],
            row["dst_service"],
            warmup_timeout_s=self._config.warmup_timeout_s,
        )
        log.info("tasks: прогрев задачи id=%s -> %s", task_id, outcome)
        if outcome == ready.READY:
            await self._emit(
                protocol.EVENT_TASK_PREWAKE, {"task_id": task_id, "meta": meta, "status": "ready"}
            )
            return
        log.warning("tasks: прогрев задачи id=%s не удался (%s), повторим на сроке", task_id, outcome)

    async def _announce_waking(self, task_id: int, meta: dict) -> None:
        if task_id in self._waking_announced:
            return
        self._waking_announced.add(task_id)
        await self._emit(
            protocol.EVENT_TASK_PREWAKE, {"task_id": task_id, "meta": meta, "status": "waking"}
        )

    async def fire_loop(self) -> None:
        while True:
            await self._sleep(POLL_INTERVAL_S)
            try:
                await self.fire_due()
            except Exception:
                log.exception("tasks: сбой опроса очереди")

    async def fire_due(self) -> None:
        now = datetime.now(tz=UTC)
        for row in await self._store.due_tasks(now):
            await self._store.mark_task_fired(row["id"], now)
            await self._store.pop_event_waiter(row["id"])
            self.fire(row)

    def fire(self, row: dict) -> None:
        self._spawn(self._fire_one_safe(row), f"task-fire-{row['id']}")

    async def _fire_one_safe(self, row: dict) -> None:
        try:
            await self._fire_one(row)
        except Exception:
            log.exception("tasks: сбой срабатывания задачи id=%s", row["id"])
        finally:
            self._waking_announced.discard(row["id"])

    async def _result(self, task_id: int, meta: dict, **outcome: Any) -> None:
        await self._emit(protocol.EVENT_TASK_RESULT, {"task_id": task_id, "meta": meta, **outcome})

    async def _fire_one(self, row: dict) -> None:
        task_id = row["id"]
        dst = Address(node=row["dst_node"], service=row["dst_service"])
        meta = json.loads(row["meta_json"])
        job = self._prewake_inflight.get(task_id)
        if job is not None and not job.done():
            log.info("tasks: задача id=%s ждёт свой идущий прогрев", task_id)
            await asyncio.wait([job], timeout=FIRE_GRACE_S)

        args = json.loads(row["args_json"])
        kind = self._kinds.get(row["action"])
        if kind is None:
            try:
                result = await self._link.command(row["action"], args, dst=dst, timeout=row["timeout_s"])
            except PEER_ERRORS as exc:
                await self._result(task_id, meta, ok=False, error=str(exc))
                return
            await self._result(task_id, meta, ok=True, result=result)
            return

        deadline = self._clock() + FIRE_GRACE_S
        if kind.wait_ready and not await self._wait_ready(row, meta, deadline):
            log.warning("tasks: задача id=%s провалена: цель так и не поднялась", task_id)
            await self._result(
                task_id, meta, ok=False, error="цель недоступна/не прогрета к моменту срабатывания"
            )
            return
        call = TaskCall(task_id, dst, args, meta, row["timeout_s"], deadline, self._link, self._emit)
        try:
            result = await self._execute(kind, call)
        except PEER_ERRORS as exc:
            log.warning("tasks: задача id=%s — исполнение не удалось: %s", task_id, exc)
            await self._result(task_id, meta, ok=False, error=str(exc))
            return
        log.info("tasks: задача id=%s исполнена", task_id)
        await self._result(task_id, meta, ok=True, result=result)

    async def _wait_ready(self, row: dict, meta: dict, deadline: float) -> bool:
        while True:
            left = max(deadline - self._clock(), 0.0)
            outcome = await ready.ensure_service_ready(
                self._link,
                self._cache,
                row["dst_node"],
                row["dst_service"],
                wake_timeout_s=min(ready.WAKE_POLL_TIMEOUT_S, left),
                warmup_timeout_s=left,
            )
            if outcome == ready.READY:
                return True
            await self._announce_waking(row["id"], meta)
            if deadline - self._clock() <= FIRE_RETRY_INTERVAL_S:
                return False
            log.warning(
                "tasks: задача id=%s — цель не готова (%s), повтор через %.0fс",
                row["id"],
                outcome,
                FIRE_RETRY_INTERVAL_S,
            )
            await self._sleep(FIRE_RETRY_INTERVAL_S)

    async def _execute(self, kind: TaskKind, call: TaskCall) -> dict[str, Any]:
        while True:
            try:
                return await kind.execute(call)
            except PEER_ERRORS as exc:
                if not kind.retry(exc) or call.deadline - self._clock() <= FIRE_RETRY_INTERVAL_S:
                    raise
                log.warning(
                    "tasks: задача id=%s — повтор через %.0fс (%s)",
                    call.task_id,
                    FIRE_RETRY_INTERVAL_S,
                    exc,
                )
                await self._sleep(FIRE_RETRY_INTERVAL_S)
