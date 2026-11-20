from __future__ import annotations

import os

import pytest

from sa_home_node import __version__, host
from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.describe import ActionParam, ActionSpec
from sa_home_node.proto.server import ProtoServer
from sa_home_node.proto.wire import ERR_UNKNOWN_ACTION, ProtoError
from sa_home_node.swarm.events import ACTION_SWARM_JOIN
from sa_home_node.swarm.service import ACTION_JOIN, NodeService


class FakeHost:
    def system_uptime_s(self) -> float | None:
        return 1234.0


class FakeRouter:
    def links_table(self) -> dict:
        return {"node-b": {"rtt_ms": 100.0, "loss": 0.0, "alive": True}}

    def peers_state(self) -> list[dict]:
        return [{"id": "node-b", "alive": True}]


def make_service(**kw: object) -> NodeService:
    return NodeService("node-a", **kw)


async def noop(args: dict) -> dict:
    return {"pong": args.get("topic")}


def ping_spec() -> ActionSpec:
    return ActionSpec(
        id="ping",
        title="Пинг",
        params=(ActionParam(name="topic", required=False),),
    )


async def test_describe_without_router() -> None:
    svc = NodeService(
        "node-a", kind="worker", own_endpoint=f"tcp://127.0.0.1:{os.getpid()}"
    )
    assert svc.advertised() == [svc._own_endpoint]
    info = svc.describe().info
    assert info.node == "node-a"
    assert info.service == "node"
    assert info.version == __version__
    assert info.node_kind == "worker"
    assert info.wake is None
    assert info.links is None
    assert info.endpoints == tuple(svc.advertised())
    desc = svc.describe()
    assert desc.capabilities == ("supervisor", "power")
    assert [a.id for a in desc.actions] == [ACTION_SWARM_JOIN, ACTION_JOIN]


async def test_live_advertise_wins() -> None:
    svc = NodeService(
        "node-a",
        own_endpoint="tcp://127.0.0.1:1",
        advertise=lambda: ["tcp://127.0.0.1:9"],
    )
    assert svc.advertised() == ["tcp://127.0.0.1:9"]
    assert svc.describe().info.endpoints == ("tcp://127.0.0.1:9",)


async def test_empty_advertise_falls_back() -> None:
    svc = NodeService("node-a", own_endpoint="tcp://127.0.0.1:1", advertise=list)
    assert svc.advertised() == ["tcp://127.0.0.1:1"]


async def test_describe_with_router() -> None:
    router = FakeRouter()
    svc = NodeService("node-a", router=router)  # type: ignore[arg-type]
    assert svc.describe().info.links == router.links_table()


async def test_describe_actions_in_order() -> None:
    svc = make_service()
    svc.actions.add(ActionSpec(id="b", title="B"), noop)
    svc.actions.add(ActionSpec(id="a", title="A"), noop)
    assert [s.id for s in svc.describe().actions] == ["b", "a"]


async def test_get_state_keys_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "current", lambda: FakeHost())
    svc = make_service()
    svc.state_parts["power"] = lambda: {"charge": 87}
    state = await svc.get_state()
    assert list(state) == [
        "node",
        "service",
        "version",
        "kind",
        "uptime_s",
        "system_uptime_s",
        "power",
        "peers",
        "wake",
    ]
    assert state["node"] == "node-a"
    assert state["service"] == "node"
    assert state["version"] == __version__
    assert state["kind"] == ""
    assert state["system_uptime_s"] == 1234.0
    assert state["power"] == {"charge": 87}
    assert state["peers"] == []
    assert state["wake"] is None


async def test_get_state_router_and_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "current", lambda: FakeHost())
    svc = make_service(router=FakeRouter())  # type: ignore[arg-type]
    svc.state_parts["battery"] = lambda: {"level": 1}
    svc.state_parts["thermal"] = lambda: {"cpu": 40}
    state = await svc.get_state()
    assert list(state) == [
        "node",
        "service",
        "version",
        "kind",
        "uptime_s",
        "system_uptime_s",
        "battery",
        "thermal",
        "peers",
        "wake",
    ]
    assert state["peers"] == [{"id": "node-b", "alive": True}]


async def test_get_state_with_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "current", lambda: FakeHost())
    svc = make_service()
    svc.attach_discovery(lambda: {"found": 2})
    state = await svc.get_state()
    assert list(state)[-1] == "discovery"
    assert state["discovery"] == {"found": 2}


async def test_failing_state_part_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "current", lambda: FakeHost())

    def broken() -> dict:
        raise RuntimeError("падаю")

    svc = make_service()
    svc.state_parts["broken"] = broken
    svc.state_parts["ok"] = lambda: {"fine": True}
    state = await svc.get_state()
    assert state["broken"] is None
    assert state["ok"] == {"fine": True}


async def test_run_command_calls_table() -> None:
    svc = make_service()
    svc.actions.add(ping_spec(), noop)
    assert await svc.run_command("ping", {"topic": "x"}) == {"pong": "x"}


async def test_run_command_unknown() -> None:
    svc = make_service()
    with pytest.raises(ProtoError) as exc:
        await svc.run_command("ghost", {})
    assert exc.value.code == ERR_UNKNOWN_ACTION


@pytest.fixture
async def node_svc():
    svc = make_service(kind="supervisor")
    svc.actions.add(ping_spec(), noop)
    svc.state_parts["power"] = lambda: {"charge": 87}
    svc.attach_discovery(lambda: {"found": 2})
    yield svc


@pytest.fixture
async def node_pair(sock_dir: str, node_svc):
    srv = ProtoServer(os.path.join(sock_dir, "node.sock"), node_svc)
    await srv.start()
    client = ProtoClient(os.path.join(sock_dir, "node.sock"), timeout=5.0)
    await client.connect()
    try:
        yield srv, client
    finally:
        await client.close()
        await srv.stop()


async def test_hello_over_socket(node_pair) -> None:
    _srv, client = node_pair
    info = await client.hello()
    assert info.node == "node-a"
    assert info.service == "node"
    assert info.version == __version__


async def test_describe_over_socket(node_pair) -> None:
    _srv, client = node_pair
    desc = await client.describe()
    assert [s.id for s in desc.actions] == ["ping"]
    assert desc.capabilities == ("supervisor", "power")


async def test_get_state_over_socket(node_pair) -> None:
    _srv, client = node_pair
    state = await client.get_state()
    assert state["power"] == {"charge": 87}
    assert state["discovery"] == {"found": 2}
    assert state["peers"] == []


async def test_command_over_socket(node_pair) -> None:
    _srv, client = node_pair
    result = await client.command("ping", {"topic": "net"})
    assert result == {"pong": "net"}


async def test_unknown_action_over_socket(node_pair) -> None:
    _srv, client = node_pair
    with pytest.raises(ProtoError) as exc:
        await client.command("ghost", {})
    assert exc.value.code == ERR_UNKNOWN_ACTION
