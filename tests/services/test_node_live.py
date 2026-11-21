from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from support import TOKEN, free_port, install_example_entry_point, wait_for

from sa_home_node import node
from sa_home_node.example import PLUGIN as EXAMPLE
from sa_home_node.plugins import Plugin, ServiceContext, ServiceSpec
from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.wire import Address, ProtoError

pytestmark = [
    pytest.mark.skipif(sys.platform != "linux", reason="unix"),
    pytest.mark.timeout(90),
]

NODE_IDS = ("node-a", "node-b")
PACKAGE = "svc-s.one.toml"


@dataclass
class Runs:
    started: list[tuple[str, str]] = field(default_factory=list)
    running: set[str] = field(default_factory=set)


def live_plugin(runs: Runs) -> Plugin:
    async def run(ctx: ServiceContext) -> None:
        text = ctx.package_path.read_text() if ctx.package_path and ctx.package_path.exists() else ""
        runs.started.append((ctx.node_id, text))
        runs.running.add(ctx.node_id)
        try:
            await asyncio.Event().wait()
        finally:
            runs.running.discard(ctx.node_id)

    return Plugin(name="live", services=(ServiceSpec("svc-s", run, singleton=True, instanced=True),))


class LiveNode:
    def __init__(self, node_id: str, root: Path, sock_dir: str, port: int, peer_port: int, assignments) -> None:
        self.id = node_id
        self.root = root
        self.port = port
        self.stop = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.sock = f"{sock_dir}/{node_id}.sock"
        self.echo_sock = f"{sock_dir}/{node_id}-echo.sock"
        peer = NODE_IDS[1 - NODE_IDS.index(node_id)]
        root.mkdir()
        (root / "content").mkdir()
        (root / "content" / "example.toml").write_text('greeting = "Привет"\n')
        listed = ", ".join(f'"{a}"' for a in assignments)
        (root / "config.toml").write_text(
            f'[node]\nid = "{node_id}"\nsocket = "{self.sock}"\nlisten = ["tcp://127.0.0.1:{port}"]\n'
            f'state_path = "{root}/state.json"\nplugins = ["example"]\n'
            f'data_dir = "{root}/data"\ncontent_dir = "{root}/content"\nassignments = [{listed}]\n'
            f'restart_delay_s = 0.2\nstop_timeout_s = 5\n'
            f'[example]\nsocket = "{self.echo_sock}"\n'
            f'[swarm]\ntoken = "{TOKEN}"\nfailover_grace_s = 1\nlease_poll_interval_s = 0.3\n'
            f'lease_settle_s = 5\nreplication_poll_interval_s = 0.3\n'
            f'[swarm.discovery]\nenabled = false\n'
            f'[[swarm.nodes]]\nid = "{peer}"\nendpoint = "tcp://127.0.0.1:{peer_port}"\n'
        )

    @property
    def package(self) -> Path:
        return self.root / "instances" / PACKAGE

    def start(self) -> None:
        self.stop = asyncio.Event()
        self.task = asyncio.create_task(node.run_node(self.root / "config.toml", stop=self.stop))

    async def shutdown(self) -> None:
        self.stop.set()
        if self.task is not None:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(self.task, timeout=30)
            self.task = None

    async def client(self) -> ProtoClient:
        client = ProtoClient(self.sock)
        await client.connect()
        return client

    async def state(self) -> dict:
        client = await self.client()
        try:
            return await client.get_state()
        finally:
            await client.close()

    async def ready(self) -> bool:
        async def up() -> bool:
            try:
                await self.state()
            except OSError:
                return False
            return True

        deadline = asyncio.get_running_loop().time() + 20
        while asyncio.get_running_loop().time() < deadline:
            if await up():
                return True
            await asyncio.sleep(0.1)
        return False


@pytest.fixture
def runs() -> Runs:
    return Runs()


@pytest.fixture
async def make_pair(tmp_path, sock_dir, monkeypatch, runs):
    install_example_entry_point(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(node, "load_plugins", lambda names: [EXAMPLE, live_plugin(runs)])
    monkeypatch.setattr(node, "arm_hard_exit", lambda total: None)
    nodes: list[LiveNode] = []

    def make(assignments=()) -> list[LiveNode]:
        ports = [free_port(), free_port()]
        for index, node_id in enumerate(NODE_IDS):
            nodes.append(
                LiveNode(node_id, tmp_path / node_id, sock_dir, ports[index], ports[1 - index], assignments)
            )
        return nodes

    yield make
    await asyncio.gather(*(n.shutdown() for n in nodes), return_exceptions=True)


@pytest.fixture
async def pair(make_pair) -> AsyncIterator[list[LiveNode]]:
    nodes = make_pair()
    for n in nodes:
        n.start()
    for n in nodes:
        assert await n.ready()
    yield nodes


async def test_assign_echo_and_route_via_peer(pair) -> None:
    first, second = pair
    client = await second.client()
    try:
        await client.command("assign", {"name": "echo"})
        assert await wait_for(lambda: Path(second.echo_sock).exists(), timeout=20)
        state = await client.get_state()
    finally:
        await client.close()
    slots = {s["name"]: s for s in state["services"]}
    assert slots["echo"]["status"] == "running"
    assert slots["echo"]["service"] == "echo"

    caller = await first.client()
    try:
        result = None
        for _ in range(100):
            try:
                result = await caller.command(
                    "echo", {"text": "эхо"}, dst=Address(node=second.id, service="echo")
                )
                break
            except (ProtoError, ConnectionError, OSError, TimeoutError):
                await asyncio.sleep(0.2)
    finally:
        await caller.close()
    assert result == {"text": "эхо"}


async def test_singleton_lease_moves_to_other_node(make_pair, runs) -> None:
    first, second = make_pair(["svc-s"])
    first.start()
    second.start()
    assert await wait_for(lambda: len(runs.running) == 1, timeout=30)
    await asyncio.sleep(1.5)
    assert runs.running == {first.id}
    assert len(runs.started) == 1

    await first.shutdown()
    assert await wait_for(lambda: runs.running == {second.id}, timeout=30)
    state = await second.state()
    assert state["singletons"]["svc-s"]["running"] is True


async def test_package_edit_replicates_and_restarts_active(make_pair, runs) -> None:
    first, second = make_pair(["svc-s@one"])
    first.package.parent.mkdir()
    first.package.write_text('value = 1\n')
    first.start()
    second.start()
    assert await wait_for(lambda: runs.running == {first.id}, timeout=30)
    assert await wait_for(lambda: second.package.exists(), timeout=30)
    assert second.package.read_text() == 'value = 1\n'
    assert runs.started == [(first.id, 'value = 1\n')]

    second.package.write_text('value = 2\n')
    assert await wait_for(lambda: len(runs.started) == 2, timeout=30)
    assert runs.started[1] == (first.id, 'value = 2\n')
    assert first.package.read_bytes() == second.package.read_bytes()
    assert runs.running == {first.id}
