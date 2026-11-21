from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
FIXED_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


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


class Collector:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.raise_on = ""

    async def __call__(self, name: str, payload: dict) -> None:
        self.events.append((name, payload))
        if name == self.raise_on:
            raise RuntimeError("emit сломан")


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


def make_service(name: str, argv: list[str], collector: Collector, host: FakeHost, **kwargs) -> process.ProcessService:
    options = {"restart_delay_s": 0.01, "stop_timeout_s": 0.1, **kwargs}
    return process.ProcessService(name, argv, emit=collector, host=host, **options)


async def wait_until(cond: Callable[[], bool], timeout_s: float = 2.0) -> None:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if cond():
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("событие не пришло вовремя")


def has_event(events: list[tuple[str, dict]], name: str) -> bool:
    return any(n == name for n, _ in events)


async def test_start_and_soft_stop(spawn: tuple) -> None:
    queue, calls = spawn
    host = FakeHost()
    proc = FakeProc()
    queue.append(proc)
    collector = Collector()
    spawned_names: list[str] = []
    svc = make_service(
        "svc", ["svc.py"], collector, host, on_spawned=spawned_names.append
    )
    await svc.start()
    await wait_until(lambda: has_event(collector.events, EVENT_SERVICE_STARTED))
    assert spawned_names == ["svc"]
    assert svc.pid == 4101
    assert svc.process_alive
    assert svc.status == RUNNING
    assert calls[0][0] == ("svc.py",)
    assert calls[0][1] == {"x": 1}

    await svc.stop()
    await wait_until(lambda: has_event(collector.events, EVENT_SERVICE_STOPPED))
    assert svc.status == STOPPED
    assert not svc.process_alive
    assert proc.calls == ["terminate"]
    assert host.terminate_targets == [proc]


async def test_restart_after_crash(spawn: tuple) -> None:
    queue, calls = spawn
    host = FakeHost()
    first, second = FakeProc(4101), FakeProc(4102)
    queue.extend([first, second])
    collector = Collector()
    svc = make_service("svc", ["svc.py"], collector, host)
    await svc.start()
    first.finish(1)
    await wait_until(
        lambda: any(
            n == EVENT_SERVICE_FAILED and p.get("exit_code") == 1 for n, p in collector.events
        )
    )
    assert svc.restarts == 1
    await wait_until(lambda: len(calls) == 2)
    assert len(calls) == 2
    assert svc.pid == 4102
    assert svc.status == RUNNING
    await svc.stop()


async def test_fenced_not_restarted(spawn: tuple) -> None:
    queue, calls = spawn
    host = FakeHost()
    proc = FakeProc()
    queue.extend([proc, FakeProc(4102)])
    collector = Collector()
    fenced_names: list[str] = []
    svc = make_service("svc-s", ["svc.py"], collector, host, on_fenced=fenced_names.append)
    await svc.start()
    proc.finish(FENCED_EXIT_CODE)
    await wait_until(lambda: has_event(collector.events, EVENT_SERVICE_FENCED))
    assert fenced_names == ["svc-s"]
    assert svc.status == STOPPED
    assert svc.last_exit_code == FENCED_EXIT_CODE
    assert len(calls) == 1
    await svc.start()
    assert len(calls) == 2
    assert svc.status == RUNNING
    await svc.stop()


async def test_hard_kill_on_terminate_timeout(spawn: tuple) -> None:
    queue, _calls = spawn
    host = FakeHost()
    proc = FakeProc()
    proc.terminate_finishes = False
    queue.append(proc)
    collector = Collector()
    svc = make_service("svc", ["svc.py"], collector, host)
    await svc.start()
    await svc.stop()
    await wait_until(lambda: has_event(collector.events, EVENT_SERVICE_STOPPED))
    assert svc.status == STOPPED
    assert proc.calls == ["terminate", "kill"]
    assert proc.returncode == 9


async def test_spawn_oserror_reports_failure(spawn: tuple) -> None:
    _queue, _calls = spawn
    host = FakeHost()
    collector = Collector()
    svc = make_service("svc", ["svc.py"], collector, host)
    await svc.start()
    await wait_until(lambda: any(n == EVENT_SERVICE_FAILED and p.get("error") for n, p in collector.events))
    assert svc.status == RESTARTING
    await svc.stop()
    await wait_until(lambda: has_event(collector.events, EVENT_SERVICE_STOPPED))
    assert svc.status == STOPPED


def raising(_name: str) -> None:
    raise RuntimeError("колбэк сломан")


@pytest.mark.parametrize("broken", ["emit", "on_spawned"])
async def test_broken_start_callbacks_do_not_double_spawn(spawn: tuple, broken: str) -> None:
    queue, calls = spawn
    host = FakeHost()
    first, second = FakeProc(4101), FakeProc(4102)
    queue.extend([first, second])
    collector = Collector()
    options = {}
    if broken == "emit":
        collector.raise_on = EVENT_SERVICE_STARTED
    else:
        options["on_spawned"] = raising
    svc = make_service("svc", ["svc.py"], collector, host, **options)
    await svc.start()
    await wait_until(lambda: has_event(collector.events, EVENT_SERVICE_STARTED))
    await asyncio.sleep(0.1)
    assert len(calls) == 1
    assert svc.pid == 4101
    assert svc.status == RUNNING
    first.finish(1)
    await wait_until(lambda: len(calls) == 2)
    assert svc.restarts == 1
    await svc.stop()
    assert svc.status == STOPPED


@pytest.mark.parametrize("error", [OSError("нет"), ValueError("плохо")])
async def test_terminate_error_falls_back_to_kill(spawn: tuple, error: Exception) -> None:
    queue, _calls = spawn
    host = FakeHost()
    host.terminate_error = error
    proc = FakeProc()
    queue.append(proc)
    collector = Collector()
    svc = make_service("svc", ["svc.py"], collector, host)
    await svc.start()
    await svc.stop()
    assert proc.calls == ["kill"]
    assert svc.status == STOPPED
    assert not svc.process_alive


async def test_broken_stopped_emit_does_not_fail_stop(spawn: tuple) -> None:
    queue, _calls = spawn
    queue.append(FakeProc())
    collector = Collector()
    collector.raise_on = EVENT_SERVICE_STOPPED
    svc = make_service("svc", ["svc.py"], collector, FakeHost())
    await svc.start()
    await svc.stop()
    assert svc.status == STOPPED


async def test_cancelled_stop_kills_and_repeat_finishes(spawn: tuple) -> None:
    queue, _calls = spawn
    proc = FakeProc()
    proc.terminate_finishes = False
    queue.append(proc)
    collector = Collector()
    svc = make_service("svc", ["svc.py"], collector, FakeHost(), stop_timeout_s=30.0)
    await svc.start()
    stopping = asyncio.create_task(svc.stop())
    await wait_until(lambda: proc.calls == ["terminate"])
    stopping.cancel()
    await asyncio.gather(stopping, return_exceptions=True)
    assert stopping.cancelled()
    assert proc.calls == ["terminate", "kill"]
    assert svc.status == STOPPED
    await asyncio.wait_for(svc.stop(), 2.0)
    assert not svc.process_alive
    await svc.start()
    assert svc.status == RUNNING or svc.status == RESTARTING
    await svc.stop()


async def test_to_dict_matches_reference(spawn: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process, "utc_now", lambda: FIXED_NOW)
    queue, _calls = spawn
    host = FakeHost()
    crash_proc, fenced_proc = FakeProc(), FakeProc()
    queue.extend([crash_proc, fenced_proc])
    collector = Collector()
    svc = make_service("svc", ["svc.py"], collector, host)
    await svc.start()
    await wait_until(lambda: has_event(collector.events, EVENT_SERVICE_STARTED))
    assert svc.to_dict() == REF["running"]

    crash_proc.finish(1)
    await wait_until(lambda: any(n == EVENT_SERVICE_FAILED and p.get("exit_code") == 1 for n, p in collector.events))
    assert svc.to_dict() == REF["restarting"]

    await svc.stop()
    await wait_until(lambda: has_event(collector.events, EVENT_SERVICE_STOPPED))
    assert svc.to_dict() == REF["stopped"]

    fenced_collector = Collector()
    fenced_names: list[str] = []
    svc_fenced = make_service(
        "svc-s", ["svc.py"], fenced_collector, host, on_fenced=fenced_names.append
    )
    await svc_fenced.start()
    fenced_proc.finish(FENCED_EXIT_CODE)
    await wait_until(lambda: has_event(fenced_collector.events, EVENT_SERVICE_FENCED))
    assert svc_fenced.to_dict() == REF["fenced"]

    svc_standby = make_service(
        "svc-i@one",
        [],
        Collector(),
        host,
        assignment=Assignment("svc-i", "one", "standby"),
    )
    assert svc_standby.to_dict() == REF["standby_instance"]
