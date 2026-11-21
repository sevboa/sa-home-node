import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sa_home_node.plugins import Plugin, ServiceSpec
from sa_home_node.proto.wire import ERR_UNAVAILABLE, ProtoError, make_response
from sa_home_node.services.assignments import Assignment, parse
from sa_home_node.services.events import (
    EVENT_SINGLETON_ACTIVATED,
    EVENT_SINGLETON_YIELDED,
    RUNNING,
    STOPPED,
)
from sa_home_node.services.lease import (
    FENCED_HOLDOFF_S,
    REASON_SUPERIOR_NOT_READY,
    SINGLETONS_KEY,
    LeaseManager,
    outranks,
    rank,
    record_rank,
)
from sa_home_node.services.registry import ServiceTable
from sa_home_node.services.supervisor import Supervisor
from sa_home_node.swarm.kind import KIND_SERVER, KIND_WORKSTATION

FIXTURE = Path(__file__).parent.parent / "fixtures" / "supervisor" / "singletons-state.json"
GRACE = 100.0
READY_TIMEOUT = 40.0


async def _run(ctx: object) -> None:
    pass


def table() -> ServiceTable:
    specs = (
        ServiceSpec(name="svc-s", run=_run, singleton=True),
        ServiceSpec(name="svc-i", run=_run, singleton=True, instanced=True),
        ServiceSpec(name="svc", run=_run),
    )
    return ServiceTable((Plugin(name="p", services=specs),))


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay
        await asyncio.sleep(0)


class FakeSlot:
    def __init__(self, text: str, *, running: bool = False, alive: bool = True) -> None:
        self.assignment: Assignment = parse(text)
        self.name = self.assignment.key
        self.status = RUNNING if running else STOPPED
        self.process_alive = alive
        self.trace: list[str] = []

    async def start(self) -> None:
        self.status = RUNNING
        self.trace.append("start")

    async def stop(self) -> None:
        self.status = STOPPED
        self.trace.append("stop")


class FakeLink:
    def __init__(self, *, alive: bool = True, down_since: float | None = None, reply: Any = None) -> None:
        self.alive = alive
        self.down_since = down_since
        self.reply = reply
        self.asked = 0

    async def forward(self, env: Any) -> Any:
        self.asked += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        if self.reply is None:
            return make_response(env, {})
        return make_response(env, {SINGLETONS_KEY: self.reply})


def build(
    *texts: str,
    kind: str = KIND_WORKSTATION,
    node: str = "node-a",
    peers: dict[str, FakeLink] | None = None,
    running: tuple[str, ...] = (),
    **kw: Any,
) -> tuple[LeaseManager, dict[str, FakeSlot], list, Clock]:
    clock = Clock()
    slots = {}

    def factory(spec: ServiceSpec, assignment: Assignment) -> FakeSlot:
        slot = FakeSlot(assignment.key, running=assignment.key in running)
        slot.assignment = assignment
        slots[assignment.key] = slot
        return slot

    supervisor = Supervisor(texts, table(), factory)
    events: list = []

    async def emit(name: str, data: dict) -> None:
        events.append((name, data))

    router = SimpleNamespace(peers=peers or {}) if peers is not None else None
    kw.setdefault("grace_s", GRACE)
    kw.setdefault("ready_timeout_s", READY_TIMEOUT)
    lease = LeaseManager(
        node, kind, supervisor, table(), router=router, emit=emit, clock=clock, sleep=clock.sleep, **kw
    )
    return lease, slots, events, clock


def peer(node: str, rank_: tuple, *, running: bool = False, claiming: bool = False, ready: bool = False) -> dict:
    return {"node": node, "rank": list(rank_), "running": running, "claiming": claiming, "ready": ready}


STATE_CASES = {
    "active_running_ready": {"texts": ("svc-s",), "running": ("svc-s",), "ready": True},
    "standby_instance": {"texts": ("svc-i@one:standby",), "kind": KIND_SERVER},
    "standby_prio": {"texts": ("svc-s:standby:prio=70",), "node": "node-b", "kind": ""},
    "fenced": {"texts": ("svc-s",), "fenced": True},
    "standby_superior_running": {"texts": ("svc-s:standby",)},
    "claiming": {"texts": ("svc-s",), "claiming": True},
}


@pytest.mark.parametrize("case", list(STATE_CASES))
def test_local_state_matches_fixture(case: str) -> None:
    params = dict(STATE_CASES[case])
    texts = params.pop("texts")
    ready = params.pop("ready", False)
    fenced = params.pop("fenced", False)
    claiming = params.pop("claiming", False)
    lease, _, _, _ = build(*texts, **params)
    if ready:
        lease.set_ready("svc-s", True)
    if fenced:
        lease.note_fenced("svc-s")
    if claiming:
        lease._claiming.add("svc-s")
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))[case]
    assert lease.local_state() == expected


def test_local_state_skips_non_singletons() -> None:
    lease, _, _, _ = build("svc", "svc-s")
    assert list(lease.local_state()) == ["svc-s"]


def test_rank_order() -> None:
    active = rank(parse("svc-s"), KIND_WORKSTATION, "node-b")
    standby = rank(parse("svc-s:standby:prio=70"), KIND_WORKSTATION, "node-a")
    assert active == (1, 10, "node-b")
    assert outranks(active, standby)
    assert outranks((0, 5, "node-a"), (0, 5, "node-b"))
    assert record_rank({"node": "node-c"}) == (0, 0, "node-c")


@pytest.mark.parametrize(
    ("text", "waited", "expect_start"),
    [
        ("svc-s", 0.0, True),
        ("svc-s:standby", GRACE - 1, False),
        ("svc-s:standby", GRACE, True),
    ],
)
async def test_free_slot_taken_by_role(text: str, waited: float, expect_start: bool) -> None:
    lease, slots, events, clock = build(text)
    slot = slots["svc-s"]
    await lease._decide(slot, [])
    clock.now += waited
    await lease._decide(slot, [])
    assert (slot.trace == ["start"]) is expect_start
    expected = [(EVENT_SINGLETON_ACTIVATED, {"slot": "svc-s", "node": "node-a"})] if expect_start else []
    assert events == expected


async def test_superior_returned_yields() -> None:
    lease, slots, events, _ = build("svc-s:standby", running=("svc-s",))
    slot = slots["svc-s"]
    await lease._decide(slot, [peer("node-b", (1, 10, "node-b"), running=True)])
    assert slot.trace == ["stop"]
    assert events == [(EVENT_SINGLETON_YIELDED, {"slot": "svc-s", "node": "node-a"})]
    assert "svc-s" in lease._yielded_since


async def test_superior_returned_but_process_already_dead() -> None:
    lease, slots, events, _ = build("svc-s:standby", running=("svc-s",))
    slot = slots["svc-s"]
    slot.process_alive = False
    await lease._decide(slot, [peer("node-b", (1, 10, "node-b"), running=True)])
    assert slot.trace == [] and events == []


async def test_superior_claiming_also_holds() -> None:
    lease, slots, _, _ = build("svc-s:standby", running=("svc-s",))
    await lease._decide(slots["svc-s"], [peer("node-b", (1, 10, "node-b"), claiming=True)])
    assert slots["svc-s"].trace == ["stop"]


async def test_running_inferior_makes_claim() -> None:
    lease, slots, _, _ = build("svc-s")
    slot = slots["svc-s"]
    await lease._decide(slot, [peer("node-b", (0, 10, "node-b"), running=True)])
    assert slot.trace == []
    assert lease.local_state()["svc-s"]["claiming"] is True


async def test_silent_superior_blocks_takeover() -> None:
    lease, slots, _, clock = build("svc-s:standby")
    slot = slots["svc-s"]
    for _ in range(2):
        await lease._decide(slot, [peer("node-b", (1, 10, "node-b"))])
        clock.now += GRACE * 2
    assert slot.trace == []


@pytest.mark.parametrize(
    ("ready", "elapsed", "expect"),
    [
        (False, READY_TIMEOUT - 1, []),
        (False, READY_TIMEOUT, ["start"]),
        (True, READY_TIMEOUT * 3, []),
    ],
)
async def test_yielded_reclaims_when_superior_not_ready(ready: bool, elapsed: float, expect: list) -> None:
    lease, slots, events, clock = build("svc-s:standby", running=("svc-s",))
    slot = slots["svc-s"]
    superior = [peer("node-b", (1, 10, "node-b"), running=True, ready=ready)]
    await lease._decide(slot, superior)
    slot.trace.clear()
    events.clear()
    clock.now += elapsed
    await lease._decide(slot, superior)
    assert slot.trace == expect
    if expect:
        assert events == [
            (
                EVENT_SINGLETON_ACTIVATED,
                {"slot": "svc-s", "node": "node-a", "reason": REASON_SUPERIOR_NOT_READY},
            )
        ]


async def test_fenced_stays_silent_for_holdoff() -> None:
    lease, slots, events, clock = build("svc-s")
    slot = slots["svc-s"]
    lease.note_fenced("svc-s")
    clock.now += FENCED_HOLDOFF_S - 1
    await lease._decide(slot, [])
    assert slot.trace == []
    clock.now += 1
    await lease._decide(slot, [])
    assert slot.trace == ["start"]
    assert events[0][0] == EVENT_SINGLETON_ACTIVATED


async def test_stuck_warning_once(caplog: pytest.LogCaptureFixture) -> None:
    lease, slots, _, clock = build("svc-s:standby", stuck_warn_s=50.0)
    slot = slots["svc-s"]
    superior = [peer("node-b", (1, 10, "node-b"))]
    caplog.set_level("WARNING")
    for _ in range(3):
        await lease._decide(slot, superior)
        clock.now += 60
    assert sum("не работает уже" in r.message for r in caplog.records) == 1


async def test_sleeping_and_silent_peers_do_not_claim() -> None:
    sleeping = FakeLink(alive=False, down_since=1.0, reply=[])
    silent_timeout = FakeLink(reply=TimeoutError())
    silent_error = FakeLink(reply=ProtoError(ERR_UNAVAILABLE, "нет"))
    lease, slots, _, _ = build(
        "svc-s",
        peers={"node-b": sleeping, "node-c": silent_timeout, "node-d": silent_error},
        running=("svc-s",),
    )
    assert await lease._peer_singletons() == {}
    assert sleeping.asked == 0
    await lease.tick()
    assert slots["svc-s"].trace == []
    assert lease.local_state()["svc-s"]["claiming"] is False


async def test_peer_state_collected_and_used() -> None:
    entry = {"role": "active", "running": True, "claiming": False, "ready": True, "rank": [1, 100, "node-b"]}
    lease, slots, events, _ = build(
        "svc-s:standby", peers={"node-b": FakeLink(reply={"svc-s": entry})}, running=("svc-s",)
    )
    assert await lease._peer_singletons() == {"svc-s": [{**entry, "node": "node-b"}]}
    await lease.tick()
    assert slots["svc-s"].trace == ["stop"]
    assert events[0][0] == EVENT_SINGLETON_YIELDED


async def test_tick_without_router_or_slots() -> None:
    lease, slots, _, _ = build("svc-s", kind=KIND_SERVER)
    assert await lease._peer_singletons() == {}
    await lease.tick()
    assert slots["svc-s"].trace == ["start"]
    empty, _, _, _ = build("svc")
    await empty.tick()


async def test_settle_returns_when_all_known() -> None:
    peers = {"node-b": FakeLink(), "node-c": FakeLink(alive=False, down_since=5.0)}
    lease, _, _, clock = build("svc-s", peers=peers)
    await lease._settle()
    assert clock.sleeps == []


async def test_settle_waits_for_unknown_link() -> None:
    link = FakeLink(alive=False)
    lease, _, _, clock = build("svc-s", peers={"node-b": link}, settle_s=10.0)
    original = clock.sleep

    async def sleep(delay: float) -> None:
        await original(delay)
        if len(clock.sleeps) == 3:
            link.alive = True

    lease._sleep = sleep
    await lease._settle()
    assert len(clock.sleeps) == 3


async def test_settle_gives_up_after_settle_s(caplog: pytest.LogCaptureFixture) -> None:
    lease, _, _, clock = build("svc-s", peers={"node-b": FakeLink(alive=False)}, settle_s=2.0)
    caplog.set_level("INFO")
    start = clock.now
    await lease._settle()
    assert clock.now - start == 2.0
    assert any("решаю по тому, что есть" in r.message for r in caplog.records)


async def test_start_ticks_then_loops_and_stops() -> None:
    lease, slots, _, clock = build("svc-s", kind=KIND_SERVER, peers={})
    ticks: list[float] = []
    real = lease.tick

    async def tick() -> None:
        ticks.append(clock.now)
        await real()

    lease.tick = tick
    await lease.start()
    assert slots["svc-s"].trace == ["start"]
    assert lease._task is not None
    await lease.stop()
    assert lease._task is None
    assert len(ticks) >= 1


async def test_run_survives_tick_error(caplog: pytest.LogCaptureFixture) -> None:
    lease, _, _, clock = build("svc-s", poll_interval_s=3.0)
    calls = 0

    async def tick() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("сбой")
        raise asyncio.CancelledError

    lease.tick = tick
    with pytest.raises(asyncio.CancelledError):
        await lease._run()
    assert calls == 2
    assert clock.sleeps == [3.0, 3.0]
    assert any("ошибка цикла" in r.message for r in caplog.records)
