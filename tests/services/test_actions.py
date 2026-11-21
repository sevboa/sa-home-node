from __future__ import annotations

import asyncio
import itertools
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from support import FakeLocalService, FakeSlot, StubService, spec, table

from sa_home_node.config import NodeConfig, NodeSection
from sa_home_node.content import ContentStore
from sa_home_node.lifecycle.power import register_power
from sa_home_node.lifecycle.update import register_update
from sa_home_node.plugins import Plugin, ServiceContext, ServiceSpec
from sa_home_node.proto.describe import ActionSpec
from sa_home_node.proto.wire import ERR_UNKNOWN_ACTION, MSG_GET_STATE, Address, ProtoError, make_request
from sa_home_node.services.actions import register_supervisor
from sa_home_node.services.assignments import Assignment
from sa_home_node.services.events import RUNNING, STOPPED
from sa_home_node.services.inproc import InProcessService, InProcessTarget
from sa_home_node.services.instances import InstanceStore
from sa_home_node.services.lease import LeaseManager
from sa_home_node.services.registry import ServiceTable
from sa_home_node.services.replication import ConfigReplicator
from sa_home_node.services.supervisor import Supervisor
from sa_home_node.swarm.router import NodeRouter
from sa_home_node.swarm.service import NodeService
from sa_home_node.swarm.state import NodeState

FIXTURES = Path(__file__).parent.parent / "fixtures" / "supervisor"
NODE = "node-a"
ENDPOINT = "tcp://192.0.2.10:8710"
TEXTS = ("svc", "svc-s", "svc-i@one:standby")
OWN_ACTIONS = {
    "start", "stop", "restart", "assign", "unassign", "report_ready", "list_instances", "get_instance_config",
    "trigger_peers", "swarm_join", "join", "restart_node", "poweroff", "reboot", "suspend", "check_update", "update",
}
REPLIES = json.loads((FIXTURES / "replies.json").read_text(encoding="utf-8"))
ERRORS = REPLIES["errors"]


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ProcSlot(FakeSlot):
    def __init__(self, assignment: Assignment, pids: Any) -> None:
        super().__init__(assignment.key)
        self.assignment = assignment
        self.pids = pids

    async def start(self) -> None:
        self.status = RUNNING
        self.pid = next(self.pids)
        self.started_at = "2026-01-02T03:04:05+00:00"

    async def stop(self) -> None:
        if self.pid is not None:
            self.last_exit_code = -15
        self.pid = None
        self.status = STOPPED

    async def restart(self) -> None:
        await self.stop()
        await self.start()


def default_table() -> ServiceTable:
    return table(
        spec("svc"),
        spec("svc-s", singleton=True, process=True, endpoint=lambda section: "./data/svc-s.sock"),
        spec("svc-i", singleton=True, instanced=True),
        spec("svc-ext", assignable=False, external=True),
    )


async def emit(name: str, data: dict) -> None:
    return None


@dataclass
class World:
    node_service: NodeService
    supervisor: Supervisor
    router: NodeRouter
    state: NodeState
    state_path: Path
    lease: LeaseManager | None
    replicator: ConfigReplicator | None
    links: list[tuple[str, str]]

    def saved(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    async def run(self, action: str, args: dict | None = None) -> dict:
        return await self.node_service.run_command(action, args or {})


async def build(
    tmp_path: Path,
    *,
    kind: str = "workstation",
    texts: tuple[str, ...] = TEXTS,
    services: ServiceTable | None = None,
    factory: Any = None,
    replicator: bool = True,
    lease: bool = True,
    first_pid: int = 4105,
    start: str | None = "svc",
    state_assignments: tuple[str, ...] = (),
) -> World:
    services = services or default_table()
    pids = itertools.count(first_pid)
    supervisor = Supervisor(texts, services, factory or (lambda found, assignment: ProcSlot(assignment, pids)))
    if start is not None:
        await supervisor.get(start).start()
    router = NodeRouter(NODE, local_services={"svc": FakeLocalService("svc", busy=True)})
    state = NodeState(assignments=list(state_assignments))
    state_path = tmp_path / "node-state.json"
    store = None
    if replicator:
        shutil.copytree(FIXTURES / "instances", tmp_path / "instances")
        store = InstanceStore(tmp_path / "instances", NODE)
    links: list[tuple[str, str]] = []

    def make_local_link(service: str, endpoint: str) -> FakeLocalService:
        links.append((service, endpoint))
        return FakeLocalService(service)

    lease_manager = LeaseManager(NODE, kind, supervisor, services, router=router, emit=emit) if lease else None
    replicator_ = ConfigReplicator(NODE, store, supervisor, services, router=router, emit=emit) if store else None
    node_service = NodeService(
        NODE, kind=kind, version="1.1.0", router=router, advertise=lambda: [ENDPOINT],
        own_endpoint=ENDPOINT, state=state, state_path=state_path,
    )
    register_supervisor(
        node_service, supervisor, services, router=router, state=state, state_path=state_path,
        make_local_link=make_local_link, lease=lease_manager, replicator=replicator_,
        config=NodeConfig(path=tmp_path / "config.toml", node=NodeSection(id=NODE), sections={}),
    )
    return World(node_service, supervisor, router, state, state_path, lease_manager, replicator_, links)


@pytest.mark.parametrize("kind", ["workstation", "server"])
async def test_describe_matches_reference(tmp_path: Path, kind: str) -> None:
    world = await build(tmp_path, kind=kind, texts=("svc", "svc-s"), start=None)
    register_power(
        world.node_service, kind=kind, power_controllable=None, request_restart=lambda: None,
        router=world.router, blockers=(),
    )
    register_update(
        world.node_service, repo="https://example.invalid/sa-home-node.git" if kind == "workstation" else None, emit=emit
    )
    expected = fixture("node-describe.json")[kind]

    async def noop(args: dict) -> dict:
        return {}

    for raw in reversed(expected["actions"]):
        if raw["id"] not in OWN_ACTIONS:
            world.node_service.actions.add(ActionSpec.from_dict(raw), noop)
    actual = world.node_service.describe().to_payload()
    assert actual == expected
    assert json.dumps(actual, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)


async def test_replies_match_reference(tmp_path: Path) -> None:
    world = await build(tmp_path)
    actions = REPLIES["actions"]
    split = next(i for i, item in enumerate(actions) if item["action"] == "unassign")
    for item in actions[:split]:
        assert await world.run(item["action"], item["args"]) == item["reply"]
    assert world.saved() == REPLIES["node_state_after_assign"]
    for item in actions[split:]:
        assert await world.run(item["action"], item["args"]) == item["reply"]
    assert world.saved() == REPLIES["node_state_after_unassign"]
    assert world.links == [("svc-s", "./data/svc-s.sock")]


def error_world_options(message: str) -> dict:
    if message.startswith("на этой ноде нет каталога"):
        return {"replicator": False}
    if "нет служб" in message:
        return {"texts": (), "start": None}
    return {}


@pytest.mark.parametrize("item", ERRORS, ids=[f"{e['action']}-{e['error']['message'][:20]}" for e in ERRORS])
async def test_errors_match_reference(tmp_path: Path, item: dict) -> None:
    world = await build(tmp_path, **error_world_options(item["error"]["message"]))
    with pytest.raises(ProtoError) as exc:
        await world.run(item["action"], item["args"])
    assert {"code": exc.value.code, "message": exc.value.message} == item["error"]


async def test_bookkeeping_follows_assignments(tmp_path: Path) -> None:
    world = await build(tmp_path, state_assignments=("ghost@x:standby",))

    def choices(action: str) -> list[str]:
        return list(world.node_service.describe().find_action(action).params[0].choices)

    assert choices("start") == ["svc", "svc-s", "svc-i@one"]
    await world.run("assign", {"name": "svc@two"})
    assert choices("stop") == ["svc", "svc-s", "svc-i@one", "svc@two"]
    assert choices("assign") == ["svc", "svc-s", "svc-i"]
    assert world.saved()["assignments"] == ["ghost@x:standby", "svc@two"]
    await world.run("unassign", {"name": "svc@two"})
    assert choices("unassign") == ["svc", "svc-s", "svc-i@one"]
    assert world.saved()["assignments"] == ["ghost@x:standby"]
    assert "svc" in world.router.local_services
    for _ in range(2):
        await world.run("assign", {"name": "svc:standby"})
    assert world.saved()["assignments"] == ["ghost@x:standby", "svc:standby"]
    assert list(world.supervisor.services) == ["svc", "svc-s", "svc-i@one"]
    await world.run("unassign", {"name": "svc"})
    assert "svc" not in world.router.local_services


@pytest.mark.parametrize(("name", "key", "kept"), [("svc@two", "svc@two", False), ("svc:standby", "svc", True)])
async def test_failed_start_rolls_back_new_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, key: str, kept: bool
) -> None:
    world = await build(tmp_path)
    before = list(world.state.assignments)

    async def boom(self: ProcSlot) -> None:
        raise RuntimeError("не стартует")

    monkeypatch.setattr(ProcSlot, "start", boom)
    with pytest.raises(RuntimeError):
        await world.run("assign", {"name": name})
    assert (key in world.supervisor.services) is kept
    assert world.state.assignments == before


async def test_without_lease_and_replicator(tmp_path: Path) -> None:
    world = await build(tmp_path, replicator=False, lease=False)
    ids = [a.id for a in world.node_service.describe().actions]
    assert "list_instances" not in ids and "get_instance_config" not in ids
    state = await world.node_service.get_state()
    assert (state["instances"], state["singletons"]) == ([], {})
    assert await world.run("report_ready", {"name": "svc"}) == {"name": "svc", "ready": True}
    with pytest.raises(ProtoError) as exc:
        await world.run("poweroff")
    assert exc.value.code == ERR_UNKNOWN_ACTION


async def test_state_parts_and_report_ready_match_lease(tmp_path: Path) -> None:
    world = await build(tmp_path, first_pid=4103)
    world.router.local_services["svc-ext"] = FakeLocalService("svc-ext")
    world.router.local_services["svc-off"] = FakeLocalService("svc-off", alive=False)
    state = await world.node_service.get_state()
    assert state["services"] == fixture("services-state.json")["node_state_services"]
    assert state["instances"] == world.replicator.local_revisions()
    assert state["instances"][0]["service"] == "svc-i"
    assert state["singletons"] == world.lease.local_state()
    assert set(state["singletons"]) == {"svc-s", "svc-i@one"}
    for ready in (True, False):
        await world.run("report_ready", {"name": "svc-i@one", "ready": ready})
        assert world.lease.local_state()["svc-i@one"]["ready"] is ready


async def test_assign_embedded_service_builds_in_process_target(tmp_path: Path) -> None:
    async def sleep_forever(ctx: object) -> None:
        await asyncio.sleep(3600)

    embedded = ServiceSpec(
        name="svc-h", run=sleep_forever, handler=lambda ctx: StubService(service="svc-h", state={"x": 1})
    )
    services = ServiceTable((Plugin(name="p", services=(embedded,)),))
    ctx = ServiceContext(node_id=NODE, data_dir=tmp_path / "svc", config=None, content=ContentStore(tmp_path))

    def factory(found: ServiceSpec, assignment: Assignment) -> InProcessService:
        return InProcessService(found, ctx, emit=emit, assignment=assignment)

    world = await build(tmp_path, texts=(), services=services, factory=factory, replicator=False, lease=False, start=None)
    await world.run("assign", {"name": "svc-h"})
    assert isinstance(world.router.local_services["svc-h"], InProcessTarget)
    reply = await world.router.route(make_request(MSG_GET_STATE, dst=Address(node=NODE, service="svc-h")))
    assert reply.payload == {"x": 1}
    await world.run("unassign", {"name": "svc-h"})
    assert "svc-h" not in world.router.local_services
    assert world.links == []
