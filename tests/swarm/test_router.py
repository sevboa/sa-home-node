from __future__ import annotations

import dataclasses
import logging
import os

import pytest
from support import StubService, wait_for

from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.describe import ActionSpec, ServiceInfo
from sa_home_node.proto.server import ProtoServer
from sa_home_node.proto.wire import (
    ERR_UNAVAILABLE,
    ERR_UNKNOWN_ACTION,
    ERR_UNKNOWN_DST,
    MSG_COMMAND,
    MSG_GET_STATE,
    Address,
    Envelope,
    ProtoError,
    make_request,
    make_response,
)
from sa_home_node.swarm.link import PeerLink
from sa_home_node.swarm.router import NodeRouter


class FakeClient:
    def __init__(self, name: str, *, broken: bool = False) -> None:
        self.name = name
        self.broken = broken
        self.seen: list[Envelope] = []

    async def forward(self, env: Envelope) -> Envelope:
        self.seen.append(env)
        if self.broken:
            raise ConnectionError("обрыв")
        return make_response(env, {"answered_by": self.name})


def make_link(
    name: str, *, rtt: float | None = None, misses: int = 0, alive: bool = True
) -> PeerLink:
    link = PeerLink(name, "tcp://127.0.0.1:1")
    if alive:
        link._client = FakeClient(name)
    if rtt is not None:
        for _ in range(12 - misses):
            link.note_probe(rtt)
    for _ in range(misses):
        link.note_probe(None)
    return link


def learn(link: PeerLink, **links: dict) -> None:
    link._learn_links(
        ServiceInfo(node=link.name, service="node", version="0", links=links)
    )


def router_for(*links: PeerLink) -> NodeRouter:
    return NodeRouter("node-a", peers={link.name: link for link in links})


def request_to(node: str, type_: str = MSG_GET_STATE) -> Envelope:
    return make_request(type_, dst=Address(node=node, service="node"))


class FakeLocalService:
    def __init__(
        self,
        name: str,
        *,
        alive: bool = True,
        busy: bool = False,
        working: bool | None = None,
    ) -> None:
        self.name = name
        self.alive = alive
        self.busy = busy
        self.working = working
        self.started = False
        self.stopped = False
        self.seen: list[Envelope] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def forward(self, env: Envelope) -> Envelope:
        self.seen.append(env)
        return make_response(env, {"service": self.name})


async def test_direct_healthy_path_used() -> None:
    target = make_link("node-b", rtt=190)
    relay = make_link("node-c", rtt=95)
    learn(relay, **{"node-b": {"rtt_ms": 85.0, "loss": 0.0, "alive": True}})
    router = router_for(target, relay)
    resp = await router.route(request_to("node-b"))
    assert resp.payload == {"answered_by": "node-b"}


async def test_degraded_direct_path_uses_relay() -> None:
    target = make_link("node-b", rtt=600, misses=2)
    relay = make_link("node-c", rtt=95)
    learn(relay, **{"node-b": {"rtt_ms": 85.0, "loss": 0.0, "alive": True}})
    router = router_for(target, relay)
    resp = await router.route(request_to("node-b"))
    assert resp.payload == {"answered_by": "node-c"}
    assert relay._client.seen[0].hops == 1
    state = router.peers_state()
    b_state = next(s for s in state if s["id"] == "node-b")
    c_state = next(s for s in state if s["id"] == "node-c")
    assert b_state["via"] == "node-c"
    assert b_state["via_rtt_ms"] == 180.0
    assert c_state["via"] is None


async def test_small_gain_keeps_direct_path() -> None:
    target = make_link("node-b", rtt=200)
    relay = make_link("node-c", rtt=95)
    learn(relay, **{"node-b": {"rtt_ms": 85.0, "loss": 0.0, "alive": True}})
    router = router_for(target, relay)
    resp = await router.route(request_to("node-b"))
    assert resp.payload == {"answered_by": "node-b"}


async def test_hysteresis_switches_back_after_sustained_improvement() -> None:
    target = make_link("node-b", rtt=600)
    relay = make_link("node-c", rtt=95)
    learn(relay, **{"node-b": {"rtt_ms": 85.0, "loss": 0.0, "alive": True}})
    router = router_for(target, relay)
    resp = await router.route(request_to("node-b"))
    assert resp.payload == {"answered_by": "node-c"}
    for _ in range(30):
        target.note_probe(170)
    resp = await router.route(request_to("node-b"))
    assert resp.payload == {"answered_by": "node-c"}
    for _ in range(30):
        target.note_probe(100)
    resp = await router.route(request_to("node-b"))
    assert resp.payload == {"answered_by": "node-b"}


async def test_relayed_request_goes_direct_only() -> None:
    target = make_link("node-b", rtt=600)
    relay = make_link("node-c", rtt=95)
    learn(relay, **{"node-b": {"rtt_ms": 85.0, "loss": 0.0, "alive": True}})
    router = router_for(target, relay)
    relayed = dataclasses.replace(request_to("node-b"), hops=1)
    resp = await router.route(relayed)
    assert resp.payload == {"answered_by": "node-b"}


async def test_dead_direct_relay_works_for_retryable() -> None:
    target = make_link("node-b", alive=False)
    relay = make_link("node-c", rtt=95)
    learn(relay, **{"node-b": {"rtt_ms": 85.0, "loss": 0.0, "alive": True}})
    router = router_for(target, relay)
    resp = await router.route(request_to("node-b", MSG_COMMAND))
    assert resp.payload == {"answered_by": "node-c"}


async def test_relay_lost_target_direct_dead_raises_unavailable() -> None:
    target = make_link("node-b", alive=False)
    relay = make_link("node-c", rtt=95)
    learn(relay, **{"node-b": {"rtt_ms": None, "loss": 1.0, "alive": False}})
    router = router_for(target, relay)
    with pytest.raises(ProtoError) as exc:
        await router.route(request_to("node-b", MSG_GET_STATE))
    assert exc.value.code == ERR_UNAVAILABLE


async def test_neighbor_without_links_table_never_relays() -> None:
    target = make_link("node-b", rtt=600, misses=3)
    relay = make_link("node-c", rtt=95)
    learn(relay)
    router = router_for(target, relay)
    resp = await router.route(request_to("node-b"))
    assert resp.payload == {"answered_by": "node-b"}


@pytest.mark.parametrize(
    "msg_type,should_retry",
    [(MSG_GET_STATE, True), (MSG_COMMAND, False)],
)
async def test_retryable_via_relay_on_direct_failure(
    msg_type: str, should_retry: bool
) -> None:
    target = make_link("node-b", rtt=100, alive=True)
    target._client = FakeClient("node-b", broken=True)
    target.reconnect_now = lambda reason: None
    relay = make_link("node-c", rtt=95)
    learn(relay, **{"node-b": {"rtt_ms": 85.0, "loss": 0.0, "alive": True}})
    router = router_for(target, relay)
    req = request_to("node-b", msg_type)
    if should_retry:
        resp = await router.route(req)
        assert resp.payload == {"answered_by": "node-c"}
        assert len(relay._client.seen) == 1
    else:
        with pytest.raises(ProtoError) as exc:
            await router.route(req)
        assert exc.value.code == ERR_UNAVAILABLE
        assert relay._client.seen == []


async def test_links_table_returns_quality() -> None:
    target = make_link("node-b", rtt=200, misses=3)
    sleeping = make_link("node-c", alive=False)
    router = router_for(target, sleeping)
    table = router.links_table()
    assert table["node-b"] == {
        "rtt_ms": 200.0,
        "loss": 0.25,
        "alive": True,
    }
    assert table["node-c"] == {
        "rtt_ms": None,
        "loss": 0.0,
        "alive": False,
    }


async def test_peers_state_keys() -> None:
    target = make_link("node-b", rtt=200)
    router = router_for(target)
    state = router.peers_state()
    assert len(state) == 1
    keys = set(state[0].keys())
    expected = {
        "id",
        "endpoint",
        "endpoints",
        "alive",
        "kind",
        "down_s",
        "left",
        "wake",
        "rtt_ms",
        "loss",
        "via",
        "via_rtt_ms",
    }
    assert keys == expected


async def test_route_none_dst_and_local_node_returns_none() -> None:
    router = router_for()
    assert await router.route(make_request(MSG_GET_STATE, dst=None)) is None
    assert (
        await router.route(
            make_request(MSG_GET_STATE, dst=Address(node="node-a", service="node"))
        )
        is None
    )


async def test_unknown_remote_node_raises_unknown_dst() -> None:
    router = router_for(make_link("node-b"))
    with pytest.raises(ProtoError) as exc:
        await router.route(request_to("node-z"))
    assert exc.value.code == ERR_UNKNOWN_DST
    assert "node-z" in str(exc.value)
    assert "node-b" in str(exc.value)


async def test_busy_and_working_services() -> None:
    router = NodeRouter("node-a")
    svc1 = FakeLocalService("svc1", alive=True, busy=True, working=None)
    svc2 = FakeLocalService("svc2", alive=True, busy=False, working=True)
    svc3 = FakeLocalService("svc3", alive=False, busy=True, working=True)
    await router.add_local_service("svc1", svc1)
    await router.add_local_service("svc2", svc2)
    await router.add_local_service("svc3", svc3)
    assert router.busy_services() == ["svc1"]
    assert router.working_services() == ["svc1", "svc2"]


async def test_route_logs_path_changes(caplog: pytest.LogCaptureFixture) -> None:
    target = make_link("node-b", rtt=600)
    relay = make_link("node-c", rtt=95)
    learn(relay, **{"node-b": {"rtt_ms": 85.0, "loss": 0.0, "alive": True}})
    router = router_for(target, relay)
    caplog.set_level(logging.INFO)
    await router.route(request_to("node-b"))
    assert any("Маршрут к node-b: через node-c" in r.message for r in caplog.records)
    for _ in range(30):
        target.note_probe(100)
    await router.route(request_to("node-b"))
    assert any("Маршрут к node-b: напрямую" in r.message for r in caplog.records)


@pytest.fixture
async def swarm(sock_dir: str):
    remote = ProtoServer(
        os.path.join(sock_dir, "b.sock"),
        StubService(
            node="node-b",
            service="svc",
            actions=(ActionSpec(id="scan_now", title="Скан"),),
            state={"node": "node-b", "cpu_c": 47.5},
        ),
    )
    await remote.start()
    peer = PeerLink("node-b", os.path.join(sock_dir, "b.sock"), reconnect_delay=0.1)
    await peer.start()
    router = NodeRouter("node-a", peers={"node-b": peer})
    local = ProtoServer(
        os.path.join(sock_dir, "a.sock"),
        StubService(node="node-a", service="node", state={"node": "node-a", "local": True}),
        router=router.route,
    )
    await local.start()
    client = ProtoClient(os.path.join(sock_dir, "a.sock"), timeout=5.0)
    await client.connect()
    assert await wait_for(lambda: peer.alive)
    try:
        yield remote, peer, router, client
    finally:
        await client.close()
        await peer.stop()
        await local.stop()
        await remote.stop()


async def test_swarm_forwards_get_state_to_remote_service(swarm) -> None:
    _remote, _peer, _router, client = swarm
    resp = await client.get_state(dst=Address(node="node-b", service="svc"))
    assert resp == {"node": "node-b", "cpu_c": 47.5}


async def test_swarm_forwards_command_to_remote_service(swarm) -> None:
    _remote, _peer, _router, client = swarm
    resp = await client.command("scan_now", dst=Address(node="node-b", service="svc"))
    assert resp == {"accepted": True}


async def test_swarm_returns_remote_error_as_is(swarm) -> None:
    _remote, _peer, _router, client = swarm
    with pytest.raises(ProtoError) as exc:
        await client.command("no_such", dst=Address(node="node-b", service="svc"))
    assert exc.value.code == ERR_UNKNOWN_ACTION


async def test_swarm_local_dst_responds_locally(swarm) -> None:
    _remote, _peer, _router, client = swarm
    resp = await client.get_state()
    assert resp.get("local") is True
    resp = await client.get_state(dst=Address(node="node-a", service="node"))
    assert resp.get("local") is True


async def test_swarm_unknown_remote_node_raises_unknown_dst(swarm) -> None:
    _remote, _peer, _router, client = swarm
    with pytest.raises(ProtoError) as exc:
        await client.get_state(dst=Address(node="node-z", service="svc"))
    assert exc.value.code == ERR_UNKNOWN_DST


async def test_swarm_dead_peer_raises_unavailable_immediately(swarm) -> None:
    remote, peer, _router, client = swarm
    await remote.stop()
    assert await wait_for(lambda: not peer.alive)
    with pytest.raises(ProtoError) as exc:
        await client.get_state(dst=Address(node="node-b", service="svc"))
    assert exc.value.code == ERR_UNAVAILABLE


async def test_swarm_local_service_via_router(swarm) -> None:
    _remote, peer, router, client = swarm
    router.local_services["svc"] = peer
    resp = await client.get_state(dst=Address(service="svc"))
    assert resp == {"node": "node-b", "cpu_c": 47.5}
    with pytest.raises(ProtoError) as exc:
        await client.get_state(dst=Address(service="nope"))
    assert exc.value.code == ERR_UNKNOWN_DST


async def test_swarm_add_remove_peer(swarm) -> None:
    _remote, peer, router, client = swarm
    new_peer = PeerLink("node-c", str(peer.endpoint), reconnect_delay=0.1)
    await router.add_peer(new_peer)
    assert await wait_for(lambda: new_peer.alive)
    resp = await client.get_state(dst=Address(node="node-c", service="svc"))
    assert resp == {"node": "node-b", "cpu_c": 47.5}
    await router.remove_peer("node-c")
    with pytest.raises(ProtoError) as exc:
        await client.get_state(dst=Address(node="node-c", service="svc"))
    assert exc.value.code == ERR_UNKNOWN_DST


async def test_swarm_add_remove_local_service(swarm) -> None:
    _remote, peer, router, client = swarm
    extra = PeerLink("svc", str(peer.endpoint), reconnect_delay=0.1)
    await router.add_local_service("svc2", extra)
    assert await wait_for(lambda: extra.alive)
    resp = await client.get_state(dst=Address(service="svc2"))
    assert resp == {"node": "node-b", "cpu_c": 47.5}
    await router.remove_local_service("svc2")
    with pytest.raises(ProtoError) as exc:
        await client.get_state(dst=Address(service="svc2"))
    assert exc.value.code == ERR_UNKNOWN_DST
