from __future__ import annotations

import pytest

from sa_home_node.proto.wire import ERR_INTERNAL, ERR_UNKNOWN_ACTION, Address, ProtoError
from sa_home_node.wake import peers, ready

INFO = {"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.0.5", "broadcast": "10.0.0.255"}


class Link:
    def __init__(self, alive=(), asleep=(), warmup=None, wol=None, wake_up_after=None):
        self.alive = set(alive)
        self.asleep = set(asleep)
        self.warmup = warmup
        self.wol = wol
        self.commands = []
        self.wake_up_after = wake_up_after

    async def get_state(self, dst=None):
        key = (dst.node, dst.service)
        if key not in self.alive:
            raise TimeoutError
        return {"asleep": True} if key in self.asleep else {}

    async def command(self, action, args=None, dst=None, *, timeout=None):
        self.commands.append((action, args, dst))
        if action == "warmup" and self.warmup is not None:
            raise self.warmup
        if action == "send_wol":
            if self.wol is not None:
                raise self.wol
            if self.wake_up_after:
                self.alive.update(self.wake_up_after)
        return {}


@pytest.fixture(autouse=True)
def patched(monkeypatch):
    async def fetch_state(link, dst, *, timeout_s=3.0):
        try:
            return await link.get_state(dst)
        except TimeoutError:
            return None

    async def resolve(link, cache, node_id):
        return dict(INFO) if node_id == "t" and cache.get("info", True) else None

    async def waker(link, cache, target, broadcast):
        return cache.get("waker")

    monkeypatch.setattr(peers, "fetch_state", fetch_state)
    monkeypatch.setattr(peers, "resolve_wake_info", resolve)
    monkeypatch.setattr(peers, "find_lan_waker", waker)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(ready.asyncio, "sleep", no_sleep)


async def run(link, cache=None, **kw):
    phases = []

    async def on_phase(p):
        phases.append(p)

    cache = {"waker": "w"} if cache is None else cache
    result = await ready.ensure_service_ready(
        link, cache, "t", "svc", on_phase_change=on_phase, wake_timeout_s=1.0, **kw
    )
    return result, phases


async def test_warm_target():
    link = Link(alive=[("t", "svc")])
    assert await run(link) == (ready.READY, [])
    assert link.commands == []


async def test_asleep_warmup():
    link = Link(alive=[("t", "svc")], asleep=[("t", "svc")])
    assert await run(link) == (ready.READY, ["warming"])
    assert link.commands[0][0] == "warmup"


async def test_node_alive_service_dead_is_crashed():
    link = Link(alive=[("t", "node")])
    assert await run(link) == (ready.CRASHED, [])
    assert link.commands == []


async def test_wake_then_warmup_phases():
    link = Link(alive=[("w", "node")], wake_up_after=[("t", "svc")])
    assert await run(link) == (ready.READY, ["waking", "woken", "warming"])
    assert [c[0] for c in link.commands] == ["send_wol", "warmup"]
    assert link.commands[0][1] == {"mac": INFO["mac"]}
    assert link.commands[0][2] == Address(node="w", service="node")


async def test_woken_but_service_never_up():
    link = Link(alive=[("w", "node")])
    result, phases = await run(link)
    assert (result, phases) == (ready.UNREACHABLE, ["waking"])


@pytest.mark.parametrize(
    ("cache", "detail"),
    [
        ({"info": False}, "Нет данных о MAC"),
        ({"waker": None}, "Некому отправить сигнал"),
    ],
)
async def test_wake_failures(cache, detail):
    link = Link()
    outcome = await ready.wake_swarm_node_core(link, cache, "t")
    assert not outcome.ok
    assert detail in outcome.detail
    assert await run(link, cache) == (ready.UNREACHABLE, ["waking"])


async def test_waker_errors():
    outcome = await ready.wake_swarm_node_core(
        Link(wol=ProtoError(ERR_INTERNAL, "сбой")), {"waker": "w"}, "t"
    )
    assert outcome == ready.WakeOutcome(False, "❌ w: сбой")
    outcome = await ready.wake_swarm_node_core(Link(wol=ConnectionError()), {"waker": "w"}, "t")
    assert outcome == ready.WakeOutcome(False, "⚠️ Нода «w» перестала отвечать во время отправки.")


async def test_wake_success_text():
    outcome = await ready.wake_swarm_node_core(Link(), {"waker": "w"}, "t")
    assert outcome == ready.WakeOutcome(
        True,
        "🔌 Magic packet для «t» (<code>aa:bb:cc:dd:ee:ff</code>) отправлен через "
        "ноду «w». Появится в /nodes, как поднимется.",
    )


async def test_guard():
    async def deny(node):
        return f"нельзя {node}"

    async def allow(node):
        return None

    link = Link(alive=[("w", "node")])
    cache = {"waker": "w"}
    assert await ready.wake_swarm_node_core(link, cache, "t", guard=deny) == ready.WakeOutcome(
        False, "нельзя t"
    )
    assert (await ready.wake_swarm_node_core(link, cache, "t", guard=deny, force=True)).ok
    assert (await ready.wake_swarm_node_core(link, cache, "t", guard=allow)).ok


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, True),
        (ProtoError(ERR_UNKNOWN_ACTION, "нет"), True),
        (ProtoError(ERR_INTERNAL, "сбой"), False),
        (TimeoutError(), False),
        (ConnectionError(), False),
    ],
)
async def test_try_warmup(error, expected):
    link = Link(warmup=error)
    assert await ready.try_warmup(link, Address(node="t", service="svc")) is expected


async def test_warmup_failure_after_asleep():
    link = Link(alive=[("t", "svc")], asleep=[("t", "svc")], warmup=TimeoutError())
    assert await run(link) == (ready.WARMUP_FAILED, ["warming"])
