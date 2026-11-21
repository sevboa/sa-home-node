from __future__ import annotations

import asyncio
import itertools
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from support import FakeLocalService, FakeSlot, StubService

from sa_home_node.config import NodeConfig, NodeSection
from sa_home_node.content import ContentStore
from sa_home_node.lifecycle.power import register_power
from sa_home_node.lifecycle.update import register_update
from sa_home_node.plugins import Plugin, ServiceContext, ServiceSpec
from sa_home_node.proto.describe import ActionSpec
from sa_home_node.proto.wire import (
    ERR_UNKNOWN_ACTION,
    MSG_GET_STATE,
    Address,
    ProtoError,
    make_request,
)
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
STARTED_AT = "2026-01-02T03:04:05+00:00"
TEXTS = ("svc", "svc-s", "svc-i@one:standby")
OWN_ACTIONS = {
    "start",
    "stop",
    "restart",
    "assign",
    "unassign",
    "report_ready",
    "list_instances",
    "get_instance_config",
    "trigger_peers",
    "swarm_join",
    "join",
    "restart_node",
    "poweroff",
    "reboot",
    "suspend",
    "check_update",
    "update",
}


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
        self.started_at = STARTED_AT

    async def stop(self) -> None:
        if self.pid is not None:
            self.last_exit_code = -15
        self.pid = None
        self.status = STOPPED

    async def restart(self) -> None:
        await self.stop()
        await self.start()


async def run_idle(ctx: object) -> None:
    return None


def default_table() -> ServiceTable:
    specs = (
        ServiceSpec(name="svc", run=run_idle),
        ServiceSpec(
            name="svc-s",
            run=run_idle,
            singleton=True,
            process=True,
            endpoint=lambda section: "./data/svc-s.sock",
        ),
        ServiceSpec(name="svc-i", run=run_idle, singleton=True, instanced=True),
        ServiceSpec(name="svc-ext", run=run_idle, assignable=False, external=True),
    )
    return ServiceTable((Plugin(name="p", services=specs),))


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


async def build(
    tmp_path: Path,
    *,
    kind: str = "workstation",
    texts: tuple[str, ...] = TEXTS,
    table: ServiceTable | None = None,
    factory: Any = None,
    replicator: bool = True,
    lease: bool = True,
    first_pid: int = 4105,
    start: str | None = "svc",
    state_assignments: tuple[str, ...] = (),
) -> World:
    table = table or default_table()
    pids = itertools.count(first_pid)
    factory = factory or (lambda spec, assignment: ProcSlot(assignment, pids))
    supervisor = Supervisor(texts, table, factory)
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

    lease_manager = LeaseManager(NODE, kind, supervisor, table, router=router, emit=emit) if lease else None
    replicator_ = (
        ConfigReplicator(NODE, store, supervisor, table, router=router, emit=emit) if store else None
    )
    node_service = NodeService(
        NODE,
        kind=kind,
        version="1.1.0",
        router=router,
        advertise=lambda: [ENDPOINT],
        own_endpoint=ENDPOINT,
        state=state,
        state_path=state_path,
    )
    config = NodeConfig(path=tmp_path / "config.toml", node=NodeSection(id=NODE), sections={})
    register_supervisor(
        node_service,
        supervisor,
        table,
        router=router,
        state=state,
        state_path=state_path,
        make_local_link=make_local_link,
        lease=lease_manager,
        replicator=replicator_,
        config=config,
    )
    return World(node_service, supervisor, router, state, state_path, lease_manager, replicator_, links)


async def noop(args: dict) -> dict:
    return {}


@pytest.mark.parametrize("kind", ["workstation", "server"])
async def test_describe_matches_reference(tmp_path: Path, kind: str) -> None:
    world = await build(tmp_path, kind=kind, texts=("svc", "svc-s"), start=None)
    register_power(
        world.node_service,
        kind=kind,
        power_controllable=None,
        request_restart=lambda: None,
        router=world.router,
        blockers=(),
    )
    register_update(
        world.node_service,
        repo="https://example.invalid/sa-home-node.git" if kind == "workstation" else None,
        emit=emit,
    )
    expected = fixture("node-describe.json")[kind]
    for raw in reversed(expected["actions"]):
        if raw["id"] not in OWN_ACTIONS:
            world.node_service.actions.add(ActionSpec.from_dict(raw), noop)
    actual = world.node_service.describe().to_payload()
    assert actual == expected
    assert json.dumps(actual, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)


async def test_choices_follow_assignments(tmp_path: Path) -> None:
    world = await build(tmp_path)

    def choices(action: str) -> list[str]:
        spec = world.node_service.describe().find_action(action)
        return list(spec.params[0].choices)

    assert choices("start") == ["svc", "svc-s", "svc-i@one"]
    await world.node_service.run_command("assign", {"name": "svc@two"})
    assert choices("stop") == ["svc", "svc-s", "svc-i@one", "svc@two"]
    assert choices("assign") == ["svc", "svc-s", "svc-i"]
    await world.node_service.run_command("unassign", {"name": "svc@two"})
    assert choices("unassign") == ["svc", "svc-s", "svc-i@one"]


async def test_replies_match_reference(tmp_path: Path) -> None:
    world = await build(tmp_path)
    reference = fixture("replies.json")
    actions = reference["actions"]
    split = next(i for i, item in enumerate(actions) if item["action"] == "unassign")
    for item in actions[:split]:
        assert await world.node_service.run_command(item["action"], item["args"]) == item["reply"]
    assert world.saved() == reference["node_state_after_assign"]
    for item in actions[split:]:
        assert await world.node_service.run_command(item["action"], item["args"]) == item["reply"]
    assert world.saved() == reference["node_state_after_unassign"]
    assert world.links == [("svc-s", "./data/svc-s.sock")]


ERRORS = fixture("replies.json")["errors"]


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
        await world.node_service.run_command(item["action"], item["args"])
    assert {"code": exc.value.code, "message": exc.value.message} == item["error"]


async def test_catalog_actions_hidden_without_replicator(tmp_path: Path) -> None:
    world = await build(tmp_path, replicator=False)
    ids = [a.id for a in world.node_service.describe().actions]
    assert "list_instances" not in ids
    assert "get_instance_config" not in ids


async def test_unknown_action_still_unknown(tmp_path: Path) -> None:
    world = await build(tmp_path)
    with pytest.raises(ProtoError) as exc:
        await world.node_service.run_command("poweroff", {})
    assert exc.value.code == ERR_UNKNOWN_ACTION


async def test_foreign_state_rows_survive(tmp_path: Path) -> None:
    world = await build(tmp_path, state_assignments=("ghost@x:standby",))
    await world.node_service.run_command("assign", {"name": "svc@two"})
    assert world.saved()["assignments"] == ["ghost@x:standby", "svc@two"]
    await world.node_service.run_command("unassign", {"name": "svc@two"})
    assert world.saved()["assignments"] == ["ghost@x:standby"]


async def test_assign_existing_slot_appends_row_once(tmp_path: Path) -> None:
    world = await build(tmp_path)
    for _ in range(2):
        await world.node_service.run_command("assign", {"name": "svc:standby"})
    assert world.saved()["assignments"] == ["svc:standby"]
    assert list(world.supervisor.services) == ["svc", "svc-s", "svc-i@one"]


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
        await world.node_service.run_command("assign", {"name": name})
    assert (key in world.supervisor.services) is kept
    assert world.state.assignments == before


async def test_unassign_keeps_link_of_sibling_slot(tmp_path: Path) -> None:
    world = await build(tmp_path)
    await world.node_service.run_command("assign", {"name": "svc@two"})
    await world.node_service.run_command("unassign", {"name": "svc@two"})
    assert "svc" in world.router.local_services
    await world.node_service.run_command("unassign", {"name": "svc"})
    assert "svc" not in world.router.local_services


async def test_state_parts_match_reference(tmp_path: Path) -> None:
    world = await build(tmp_path, first_pid=4103)
    world.router.local_services["svc-ext"] = FakeLocalService("svc-ext")
    world.router.local_services["svc-off"] = FakeLocalService("svc-off", alive=False)
    state = await world.node_service.get_state()
    assert state["services"] == fixture("services-state.json")["node_state_services"]
    assert state["instances"] == world.replicator.local_revisions()
    assert state["instances"][0]["service"] == "svc-i"
    assert state["singletons"] == world.lease.local_state()
    assert set(state["singletons"]) == {"svc-s", "svc-i@one"}


async def test_state_parts_without_lease_and_replicator(tmp_path: Path) -> None:
    world = await build(tmp_path, replicator=False, lease=False)
    state = await world.node_service.get_state()
    assert state["instances"] == []
    assert state["singletons"] == {}


async def test_report_ready_reaches_lease(tmp_path: Path) -> None:
    world = await build(tmp_path)
    await world.node_service.run_command("report_ready", {"name": "svc-i@one", "ready": True})
    assert world.lease.local_state()["svc-i@one"]["ready"] is True
    await world.node_service.run_command("report_ready", {"name": "svc-i@one", "ready": False})
    assert world.lease.local_state()["svc-i@one"]["ready"] is False


async def test_report_ready_defaults_to_ready(tmp_path: Path) -> None:
    world = await build(tmp_path, lease=False)
    assert await world.node_service.run_command("report_ready", {"name": "svc"}) == {
        "name": "svc",
        "ready": True,
    }


async def test_assign_embedded_service_builds_in_process_target(tmp_path: Path) -> None:
    async def sleep_forever(ctx: object) -> None:
        await asyncio.sleep(3600)

    spec = ServiceSpec(
        name="svc-h",
        run=sleep_forever,
        handler=lambda ctx: StubService(service="svc-h", state={"x": 1}),
    )
    table = ServiceTable((Plugin(name="p", services=(spec,)),))
    ctx = ServiceContext(
        node_id=NODE, data_dir=tmp_path / "svc", config=None, content=ContentStore(tmp_path)
    )

    def factory(found: ServiceSpec, assignment: Assignment) -> InProcessService:
        return InProcessService(found, ctx, emit=emit, assignment=assignment)

    world = await build(
        tmp_path, texts=(), table=table, factory=factory, replicator=False, lease=False, start=None
    )
    await world.node_service.run_command("assign", {"name": "svc-h"})
    target = world.router.local_services["svc-h"]
    assert isinstance(target, InProcessTarget)
    reply = await world.router.route(make_request(MSG_GET_STATE, dst=Address(node=NODE, service="svc-h")))
    assert reply.payload == {"x": 1}
    await world.node_service.run_command("unassign", {"name": "svc-h"})
    assert "svc-h" not in world.router.local_services
    assert world.links == []
