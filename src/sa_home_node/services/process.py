from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sa_home_node.host import Host, current
from sa_home_node.services.assignments import Assignment
from sa_home_node.services.events import (
    EVENT_SERVICE_FAILED,
    EVENT_SERVICE_FENCED,
    EVENT_SERVICE_STARTED,
    EVENT_SERVICE_STOPPED,
    FENCED_EXIT_CODE,
    RESTARTING,
    RUNNING,
    STOPPED,
    EventEmitter,
    call_quietly,
    emit_quietly,
    slot_dict,
)

log = logging.getLogger(__name__)

SUPERVISE_EXIT_TIMEOUT_S = 5.0

SPAWN_TIMEOUT_S = 5.0


def utc_now() -> datetime:
    return datetime.now(UTC)


class ProcessService:
    def __init__(
        self,
        name: str,
        argv: list[str],
        *,
        emit: EventEmitter,
        restart_delay_s: float = 5.0,
        stop_timeout_s: float = 90.0,
        assignment: Assignment | None = None,
        on_fenced: Callable[[str], None] | None = None,
        on_spawned: Callable[[str], None] | None = None,
        host: Host | None = None,
    ) -> None:
        self.name = name
        self._on_fenced = on_fenced
        self._on_spawned = on_spawned
        self.assignment = assignment or Assignment(service=name)
        self._argv = argv
        self._emit = emit
        self._restart_delay = restart_delay_s
        self._stop_timeout = stop_timeout_s
        self._host = host or current()

        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self._spawned = asyncio.Event()
        self._desired_running = False
        self._status = STOPPED
        self.restarts = 0
        self.last_exit_code: int | None = None
        self.started_at: str | None = None

    @property
    def status(self) -> str:
        return self._status

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def process_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def to_dict(self) -> dict:
        return slot_dict(self)

    async def start(self) -> None:
        if self._desired_running:
            return
        if self._task is not None and not self._task.done():
            await self.stop()
        self._desired_running = True
        self._spawned.clear()
        self._task = asyncio.create_task(self._run(), name=f"supervise-{self.name}")
        self._task.add_done_callback(self._on_task_done)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._spawned.wait(), timeout=SPAWN_TIMEOUT_S)

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error(
                "Служба %s: наблюдающая задача завершилась с необработанным "
                "исключением — служба больше НЕ супервизируется",
                self.name,
                exc_info=exc,
            )

    async def stop(self) -> None:
        task = self._task
        if not self._desired_running and (task is None or task.done()):
            return
        self._desired_running = False
        try:
            await self._halt_process()
            await self._reap_task()
        except BaseException:
            self._status = STOPPED
            self._kill(self._proc)
            raise
        self._status = STOPPED
        await emit_quietly(self._emit, EVENT_SERVICE_STOPPED, {"name": self.name})

    async def _halt_process(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        try:
            self._host.terminate(proc)
            await asyncio.wait_for(proc.wait(), timeout=self._stop_timeout)
            return
        except TimeoutError:
            log.warning("Служба %s не остановилась за %.0f с — kill", self.name, self._stop_timeout)
        except Exception:
            log.warning("Служба %s: мягкий останов не удался — kill", self.name, exc_info=True)
        self._kill(proc)
        await proc.wait()

    async def _reap_task(self) -> None:
        task = self._task
        if task is None:
            return
        done, _ = await asyncio.wait({task}, timeout=SUPERVISE_EXIT_TIMEOUT_S)
        if not done:
            log.warning(
                "Служба %s: наблюдающая задача не завершилась за %.0f с — снимаю",
                self.name,
                SUPERVISE_EXIT_TIMEOUT_S,
            )
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._task = None

    @staticmethod
    def _kill(proc: asyncio.subprocess.Process | None) -> None:
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(OSError):
                proc.kill()

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _run(self) -> None:
        while self._desired_running:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self._argv, **self._host.spawn_kwargs()
                )
            except OSError as exc:
                log.error("Не удалось запустить службу %s: %s", self.name, exc)
                self._spawned.set()
                await emit_quietly(self._emit, EVENT_SERVICE_FAILED, {"name": self.name, "error": str(exc)})
                self._status = RESTARTING
                await asyncio.sleep(self._restart_delay)
                continue

            try:
                self._proc = proc
                self._status = RUNNING
                self._spawned.set()
                self.started_at = utc_now().isoformat()
                log.info("Служба %s запущена (pid=%s)", self.name, proc.pid)
                call_quietly(self._on_spawned, self.name)
                await emit_quietly(self._emit, EVENT_SERVICE_STARTED, {"name": self.name, "pid": proc.pid})

                rc = await proc.wait()
                self._proc = None
                self.last_exit_code = rc
                if not self._desired_running:
                    log.info(
                        "Служба %s: процесс завершился (код %s) во время "
                        "остановки (desired_running уже снят) — не перезапускаю",
                        self.name,
                        rc,
                    )
                    break
                if rc == FENCED_EXIT_CODE:
                    log.warning(
                        "Служба %s вытеснена другим экземпляром — не перезапускаю",
                        self.name,
                    )
                    self._desired_running = False
                    self._status = STOPPED
                    await emit_quietly(self._emit, EVENT_SERVICE_FENCED, {"name": self.name})
                    call_quietly(self._on_fenced, self.name)
                    break
                log.warning(
                    "Служба %s завершилась (код %s) — перезапуск через %.0f с",
                    self.name,
                    rc,
                    self._restart_delay,
                )
                self._status = RESTARTING
                self.restarts += 1
                await emit_quietly(self._emit, EVENT_SERVICE_FAILED, {"name": self.name, "exit_code": rc})
            except Exception:
                log.exception(
                    "Служба %s: необработанная ошибка в цикле наблюдения — "
                    "считаю падением, перезапуск через %.0f с",
                    self.name,
                    self._restart_delay,
                )
                self._proc = None
                if not self._desired_running:
                    break
                self._status = RESTARTING
                self.restarts += 1
                await emit_quietly(
                    self._emit,
                    EVENT_SERVICE_FAILED,
                    {"name": self.name, "error": "supervisor_internal_error"},
                )

            await asyncio.sleep(self._restart_delay)
