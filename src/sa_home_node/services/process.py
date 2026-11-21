from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from sa_home_node.host import Host, current
from sa_home_node.services.assignments import Assignment
from sa_home_node.services.events import (
    EVENT_SERVICE_FAILED,
    EVENT_SERVICE_STARTED,
    FENCED_EXIT_CODE,
    RESTARTING,
    RUNNING,
    STOPPED,
    EventEmitter,
    call_quietly,
)
from sa_home_node.services.slot import SlotBase, utc_now

log = logging.getLogger(__name__)

SUPERVISE_EXIT_TIMEOUT_S = 5.0
SPAWN_TIMEOUT_S = 5.0


class ProcessService(SlotBase):
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
        super().__init__(
            name, assignment or Assignment(service=name), emit=emit, restart_delay_s=restart_delay_s,
            stop_timeout_s=stop_timeout_s, on_fenced=on_fenced, on_spawned=on_spawned,
        )
        self._argv = argv
        self._host = host or current()
        self._proc: asyncio.subprocess.Process | None = None
        self._spawned = asyncio.Event()

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def process_alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

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
        if not task.cancelled() and (exc := task.exception()) is not None:
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
            if task is not None:
                await self._cancel(task, SUPERVISE_EXIT_TIMEOUT_S, "наблюдающая задача не завершилась")
                self._task = None
        except BaseException:
            self._status = STOPPED
            self._kill(self._proc)
            raise
        await self._stopped()

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

    @staticmethod
    def _kill(proc: asyncio.subprocess.Process | None) -> None:
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(OSError):
                proc.kill()

    async def _failed(self, **payload) -> None:
        self._status = RESTARTING
        await self._event(EVENT_SERVICE_FAILED, **payload)

    async def _run(self) -> None:
        while self._desired_running:
            try:
                proc = await asyncio.create_subprocess_exec(*self._argv, **self._host.spawn_kwargs())
            except OSError as exc:
                log.error("Не удалось запустить службу %s: %s", self.name, exc)
                self._spawned.set()
                await self._failed(error=str(exc))
                await asyncio.sleep(self._restart_delay)
                continue
            try:
                self._proc = proc
                self._status = RUNNING
                self._spawned.set()
                self.started_at = utc_now().isoformat()
                log.info("Служба %s запущена (pid=%s)", self.name, proc.pid)
                call_quietly(self._on_spawned, self.name)
                await self._event(EVENT_SERVICE_STARTED, pid=proc.pid)
                rc = self.last_exit_code = await proc.wait()
                self._proc = None
                if not self._desired_running:
                    log.info(
                        "Служба %s: процесс завершился (код %s) во время "
                        "остановки (desired_running уже снят) — не перезапускаю",
                        self.name,
                        rc,
                    )
                    break
                if rc == FENCED_EXIT_CODE:
                    log.warning("Служба %s вытеснена другим экземпляром — не перезапускаю", self.name)
                    await self._fenced()
                    break
                log.warning(
                    "Служба %s завершилась (код %s) — перезапуск через %.0f с",
                    self.name,
                    rc,
                    self._restart_delay,
                )
                self.restarts += 1
                await self._failed(exit_code=rc)
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
                self.restarts += 1
                await self._failed(error="supervisor_internal_error")
            await asyncio.sleep(self._restart_delay)
