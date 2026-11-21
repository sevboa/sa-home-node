from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from support import Clock, FakeLocalService, wait_for

from sa_home_node import host
from sa_home_node.lifecycle.power import (
    RESTART_DEFER_MAX_S,
    RESTART_DEFER_POLL_S,
    register_power,
    run_command,
    ssh_sessions,
    tmux_sessions,
)
from sa_home_node.proto.wire import ERR_UNKNOWN_ACTION, ProtoError
from sa_home_node.swarm.router import NodeRouter
from sa_home_node.swarm.service import NodeService

REPLIES = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "supervisor" / "replies.json").read_text(encoding="utf-8")
)
SESSION_LISTING = "1 1000 user seat0\n2 1000 user seat0\n"
SESSION_PROPS = {
    "1": "Remote=yes\nName=user\nTTY=pts/0\nTimestamp=10:00\n",
    "2": "Remote=no\nName=user\nTTY=\nTimestamp=09:00\n",
}


class FakeHost:
    def power_argv(self, action: str) -> list[str]:
        return ["fake-power", action]


class FakeCommands:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.sessions = False
        self.tmux = ""
        self.missing: set[str] = set()
        self.power_code = 0

    async def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append(argv)
        if argv[0] in self.missing:
            raise FileNotFoundError(argv[0])
        if argv[0] == "fake-power":
            return self.power_code, "", "denied"
        if argv[:2] == ["loginctl", "list-sessions"]:
            return 0, SESSION_LISTING if self.sessions else "", ""
        if argv[:2] == ["loginctl", "show-session"]:
            return 0, SESSION_PROPS[argv[2]], ""
        if argv[0] == "tmux":
            return 0, self.tmux, ""
        raise AssertionError(argv)

    def power_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "fake-power"]


class World:
    def __init__(self, kind: str, controllable: bool | None, blockers: tuple = ()) -> None:
        self.clock = Clock()
        self.hold = asyncio.Event()
        self.hold.set()
        self.commands = FakeCommands()
        self.restarts = 0
        self.svc = FakeLocalService("svc", busy=True)
        self.router = NodeRouter("node-a", local_services={"svc": self.svc})
        self.node_service = NodeService("node-a", kind=kind, router=self.router)
        register_power(
            self.node_service,
            kind=kind,
            power_controllable=controllable,
            request_restart=self.restart,
            router=self.router,
            blockers=blockers,
            run=self.commands,
            sleep=self.sleep,
        )

    async def sleep(self, delay: float) -> None:
        await self.clock.sleep(delay)
        if delay == RESTART_DEFER_POLL_S:
            await self.hold.wait()

    def restart(self) -> None:
        self.restarts += 1

    async def run(self, action: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.node_service.run_command(action, args or {})

    def action_ids(self) -> list[str]:
        return [a.id for a in self.node_service.describe().actions]


@pytest.fixture(autouse=True)
def fake_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "current", lambda: FakeHost())


@pytest.fixture
def world() -> World:
    return World("workstation", None)


@pytest.mark.parametrize(
    ("kind", "controllable", "expected"),
    [
        ("workstation", None, ["trigger_peers", "restart_node", "poweroff", "reboot", "suspend"]),
        ("server", None, ["trigger_peers", "restart_node"]),
        ("server", True, ["trigger_peers", "restart_node", "poweroff", "reboot", "suspend"]),
        ("workstation", False, ["trigger_peers", "restart_node"]),
    ],
)
async def test_registered_actions(kind: str, controllable: bool | None, expected: list[str]) -> None:
    assert World(kind, controllable).action_ids() == expected


async def test_power_replies_match_reference(world: World) -> None:
    items = REPLIES["power"]
    for item in items[:3]:
        assert await world.run(item["action"], item["args"]) == item["reply"]
    await wait_for(lambda: len(world.commands.power_calls()) == 3)
    assert world.commands.power_calls() == [["fake-power", a] for a in ("poweroff", "reboot", "suspend")]
    assert world.clock.sleeps == [1.0, 1.0, 1.0]
    assert await world.run(items[3]["action"], items[3]["args"]) == items[3]["reply"]
    world.commands.sessions = True
    world.commands.tmux = "main\n"
    assert await world.run(items[4]["action"], items[4]["args"]) == items[4]["reply"]
    world.svc.busy = False
    world.commands.sessions = False
    world.commands.tmux = ""
    before = len(world.commands.power_calls())
    assert await world.run(items[5]["action"], items[5]["args"]) == items[5]["reply"]
    await wait_for(lambda: len(world.commands.power_calls()) == before + 1)
    assert world.commands.power_calls()[-1] == ["fake-power", "poweroff"]


async def test_blocked_poweroff_runs_nothing(world: World) -> None:
    reply = await world.run("poweroff", {"if_idle": True})
    await asyncio.sleep(0)
    assert reply["done"] is False
    assert world.commands.power_calls() == []
    assert world.clock.sleeps == []


async def test_if_idle_must_be_true_boolean(world: World) -> None:
    reply = await world.run("poweroff", {"if_idle": "yes"})
    assert reply == {"scheduled": "poweroff", "delay_s": 1.0}


async def test_plugin_blockers_come_last(world: World) -> None:
    async def first() -> list[str]:
        return ["плагин: a"]

    async def second() -> list[str]:
        return []

    other = World("workstation", None, (first, second))
    other.commands.sessions = True
    other.commands.tmux = "main\n"
    reply = await other.run("poweroff", {"if_idle": True})
    assert reply["blockers"] == ["службы: svc", "user, pts/0, с 10:00", "tmux: main", "плагин: a"]


async def test_missing_session_tools_are_no_blockers(world: World) -> None:
    world.svc.busy = False
    world.commands.missing = {"loginctl", "tmux"}
    assert await world.run("poweroff", {"if_idle": True}) == {"done": True, "blockers": []}


async def test_session_defaults() -> None:
    commands = FakeCommands()
    commands.sessions = True

    async def sparse(argv: list[str]) -> tuple[int, str, str]:
        if argv[1] == "show-session":
            return 0, "Remote=yes\n", ""
        return await commands(argv)

    assert await ssh_sessions(sparse) == ["?, ?, с ?", "?, ?, с ?"]
    commands.tmux = "a\n\nb\n"
    assert await tmux_sessions(commands) == ["tmux: a", "tmux: b"]


async def test_failed_power_command_is_logged(world: World, caplog: pytest.LogCaptureFixture) -> None:
    world.commands.power_code = 1
    with caplog.at_level(logging.ERROR):
        await world.run("reboot")
        await wait_for(lambda: "denied" in caplog.text)
    assert "завершилась с кодом 1" in caplog.text


async def test_restart_replies_match_reference(world: World) -> None:
    world.hold.clear()
    items = REPLIES["restart_node"]
    for item in items[:2]:
        assert await world.run(item["action"], item["args"]) == item["reply"]
    world.svc.busy = False
    for item in items[2:]:
        assert await world.run(item["action"], item["args"]) == item["reply"]
    await wait_for(lambda: world.restarts == 2)
    assert world.clock.sleeps == [1.0, 1.0]


async def test_deferred_restart_waits_for_idle(world: World) -> None:
    world.hold.clear()
    await world.run("restart_node")
    await wait_for(lambda: world.clock.sleeps == [RESTART_DEFER_POLL_S])
    world.svc.busy = False
    world.hold.set()
    await wait_for(lambda: world.restarts == 1)
    assert world.clock.sleeps == [RESTART_DEFER_POLL_S, 1.0]


async def test_deferred_restart_gives_up_after_limit(world: World) -> None:
    await world.run("restart_node")
    await wait_for(lambda: world.restarts == 1)
    polls = int(RESTART_DEFER_MAX_S / RESTART_DEFER_POLL_S)
    assert world.clock.sleeps == [RESTART_DEFER_POLL_S] * polls + [1.0]


async def test_failed_probe_restarts_at_once(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> list[str]:
        raise RuntimeError("probe")

    monkeypatch.setattr(world.router, "working_services", broken)
    assert await world.run("restart_node") == {"scheduled": "restart_node", "delay_s": 1.0}
    await wait_for(lambda: world.restarts == 1)


async def test_failed_probe_while_waiting_restarts_forcibly(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.hold.clear()
    await world.run("restart_node")

    def broken() -> list[str]:
        raise RuntimeError("probe")

    monkeypatch.setattr(world.router, "working_services", broken)
    world.hold.set()
    await wait_for(lambda: world.restarts == 1)


async def test_unregistered_power_is_unknown() -> None:
    other = World("server", None)
    with pytest.raises(ProtoError) as exc:
        await other.run("reboot")
    assert exc.value.code == ERR_UNKNOWN_ACTION


async def test_run_command_captures_output() -> None:
    code, out, err = await run_command(
        [sys.executable, "-c", "import sys; print('x'); sys.stderr.write('y'); sys.exit(3)"]
    )
    assert (code, out.strip(), err) == (3, "x", "y")
