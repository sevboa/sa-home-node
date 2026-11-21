from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from support import Collector, wait_for

from sa_home_node.services import process
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
)

REF: dict[str, dict] = json.loads(
    (Path(__file__).parents[1] / "fixtures/supervisor/services-state.json").read_text(encoding="utf-8")
)["cases"]


class FakeProc:
    def __init__(self, pid: int = 4101):
        self.pid = pid
        self.returncode: int | None = None
        self.calls: list[str] = []
        self.terminate_finishes = True
        self._done = asyncio.Event()

    async def wait(self) -> int | None:
        await self._done.wait()
        return self.returncode

    def finish(self, rc: int) -> None:
        self.returncode = rc
        self._done.set()

    def terminate(self) -> None:
        self.calls.append("terminate")
        if self.terminate_finishes:
            self.finish(0)

    def kill(self) -> None:
        self.calls.append("kill")
        self.finish(9)


class FakeHost:
    def __init__(self):
        self.terminate_targets: list[FakeProc] = []
        self.terminate_error: Exception | None = None

    def spawn_kwargs(self) -> dict:
        return {"x": 1}

    def terminate(self, proc: FakeProc) -> None:
        self.terminate_targets.append(proc)
        if self.terminate_error is not None:
            raise self.terminate_error
        proc.terminate()


@pytest.fixture()
def spawn(monkeypatch: pytest.MonkeyPatch):
    queue: list[FakeProc] = []
    calls: list[tuple] = []

    async def fake_spawn(*args, **kwargs):
        calls.append((args, kwargs))
        if not queue:
            raise OSError("пул процессов пуст")
        return queue.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    return queue, calls


async def boot(spawn, *procs, name="svc", collector=None, host=None, **kw):
    queue, calls = spawn
    queue.extend(procs)
    collector = collector or Collector()
    host = host or FakeHost()
    options = {"restart_delay_s": 0.01, "stop_timeout_s": 0.1, **kw}
    svc = process.ProcessService(name, ["svc.py"], emit=collector, host=host, **options)
    await svc.start()
    return svc, collector, host, calls


async def test_start_and_soft_stop(spawn: tuple) -> None:
    proc, spawned = FakeProc(), []
    collector = Collector(raise_on=EVENT_SERVICE_STOPPED)
    svc, collector, host, calls = await boot(spawn, proc, on_spawned=spawned.append, collector=collector)
    await collector.seen(EVENT_SERVICE_STARTED)
    assert (spawned, svc.pid, svc.process_alive, svc.status) == (["svc"], 4101, True, RUNNING)
    assert calls == [(("svc.py",), {"x": 1})]
    await svc.stop()
    await collector.seen(EVENT_SERVICE_STOPPED)
    assert (svc.status, svc.process_alive) == (STOPPED, False)
    assert proc.calls == ["terminate"]
    assert host.terminate_targets == [proc]


async def test_restart_after_crash(spawn: tuple) -> None:
    first = FakeProc()
    svc, collector, _, calls = await boot(spawn, first, FakeProc(4102))
    first.finish(1)
    await collector.seen(EVENT_SERVICE_FAILED)
    assert collector.of(EVENT_SERVICE_FAILED)[0]["exit_code"] == 1
    assert await wait_for(lambda: len(calls) == 2 and svc.pid == 4102, 2)
    assert (svc.restarts, svc.status) == (1, RUNNING)
    await svc.stop()


async def test_fenced_not_restarted(spawn: tuple) -> None:
    proc, fenced = FakeProc(), []
    svc, collector, _, calls = await boot(spawn, proc, FakeProc(4102), name="svc-s", on_fenced=fenced.append)
    proc.finish(FENCED_EXIT_CODE)
    await collector.seen(EVENT_SERVICE_FENCED)
    assert (fenced, svc.status, svc.last_exit_code, len(calls)) == (["svc-s"], STOPPED, FENCED_EXIT_CODE, 1)
    await svc.start()
    assert (len(calls), svc.status) == (2, RUNNING)
    await svc.stop()


@pytest.mark.parametrize(
    ("finishes", "error", "expected"),
    [(False, None, ["terminate", "kill"]), (True, OSError("нет"), ["kill"]), (True, ValueError("плохо"), ["kill"])],
)
async def test_stop_escalates_to_kill(spawn: tuple, finishes: bool, error: Exception | None, expected: list) -> None:
    proc, host = FakeProc(), FakeHost()
    proc.terminate_finishes, host.terminate_error = finishes, error
    svc, *_ = await boot(spawn, proc, host=host)
    await svc.stop()
    assert (proc.calls, proc.returncode) == (expected, 9)
    assert (svc.status, svc.process_alive) == (STOPPED, False)


async def test_spawn_failure_reports_and_retries(spawn: tuple) -> None:
    svc, collector, *_ = await boot(spawn)
    assert await wait_for(lambda: any(p.get("error") for p in collector.of(EVENT_SERVICE_FAILED)), 2)
    assert svc.status == RESTARTING
    await svc.stop()
    assert svc.status == STOPPED


def raising(_name: str) -> None:
    raise RuntimeError("колбэк сломан")


@pytest.mark.parametrize("broken", ["emit", "on_spawned"])
async def test_broken_start_callbacks_do_not_double_spawn(spawn: tuple, broken: str) -> None:
    first = FakeProc()
    options = {"on_spawned": raising} if broken == "on_spawned" else {"collector": Collector(raise_on=EVENT_SERVICE_STARTED)}
    svc, collector, _, calls = await boot(spawn, first, FakeProc(4102), **options)
    await collector.seen(EVENT_SERVICE_STARTED)
    await asyncio.sleep(0.1)
    assert (len(calls), svc.pid, svc.status) == (1, 4101, RUNNING)
    first.finish(1)
    assert await wait_for(lambda: len(calls) == 2, 2)
    assert svc.restarts == 1
    await svc.stop()
    assert svc.status == STOPPED


async def test_cancelled_stop_kills_and_repeat_finishes(spawn: tuple) -> None:
    proc = FakeProc()
    proc.terminate_finishes = False
    svc, *_ = await boot(spawn, proc, stop_timeout_s=30.0)
    stopping = asyncio.create_task(svc.stop())
    assert await wait_for(lambda: proc.calls == ["terminate"], 2)
    stopping.cancel()
    await asyncio.gather(stopping, return_exceptions=True)
    assert stopping.cancelled()
    assert (proc.calls, svc.status) == (["terminate", "kill"], STOPPED)
    await asyncio.wait_for(svc.stop(), 2.0)
    assert not svc.process_alive
    await svc.start()
    assert svc.status in (RUNNING, RESTARTING)
    await svc.stop()


async def test_to_dict_matches_reference(spawn: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process, "utc_now", lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
    crash, fenced_proc = FakeProc(), FakeProc()
    svc, collector, host, _ = await boot(spawn, crash, fenced_proc)
    await collector.seen(EVENT_SERVICE_STARTED)
    assert svc.to_dict() == REF["running"]
    crash.finish(1)
    await collector.seen(EVENT_SERVICE_FAILED)
    assert svc.to_dict() == REF["restarting"]
    await svc.stop()
    await collector.seen(EVENT_SERVICE_STOPPED)
    assert svc.to_dict() == REF["stopped"]
    fenced, fenced_collector, *_ = await boot(spawn, name="svc-s", host=host, on_fenced=lambda name: None)
    fenced_proc.finish(FENCED_EXIT_CODE)
    await fenced_collector.seen(EVENT_SERVICE_FENCED)
    assert fenced.to_dict() == REF["fenced"]
    standby = process.ProcessService(
        "svc-i@one", [], emit=Collector(), host=host, assignment=Assignment("svc-i", "one", "standby")
    )
    assert standby.to_dict() == REF["standby_instance"]
