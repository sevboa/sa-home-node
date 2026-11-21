import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from support import Clock, Collector, FakeSlot, slot_factory, spec, table

from sa_home_node.proto.wire import ERR_UNAVAILABLE, ProtoError, make_response
from sa_home_node.services.assignments import parse
from sa_home_node.services.events import EVENT_SINGLETON_ACTIVATED, EVENT_SINGLETON_YIELDED
from sa_home_node.services.lease import (
    FENCED_HOLDOFF_S,
    REASON_SUPERIOR_NOT_READY,
    SINGLETONS_KEY,
    LeaseManager,
    outranks,
    rank,
    record_rank,
)
from sa_home_node.services.supervisor import Supervisor
from sa_home_node.swarm.kind import KIND_SERVER, KIND_WORKSTATION

FIXTURE = Path(__file__).parent.parent / "fixtures" / "supervisor" / "singletons-state.json"
GRACE = 100.0
READY_TIMEOUT = 40.0
TABLE = table(spec("svc-s", singleton=True), spec("svc-i", singleton=True, instanced=True), spec("svc"))
GOOD = {"role": "active", "running": True, "claiming": False, "ready": True, "rank": [1, 100, "node-c"]}


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
        return make_response(env, {} if self.reply is None else {SINGLETONS_KEY: self.reply})


def build(
    *texts: str,
    kind: str = KIND_WORKSTATION,
    node: str = "node-a",
    peers: dict[str, FakeLink] | None = None,
    running: tuple[str, ...] = (),
    **kw: Any,
) -> tuple[LeaseManager, dict[str, FakeSlot], Collector, Clock]:
    clock, slots, events = Clock(), {}, Collector()
    supervisor = Supervisor(texts, TABLE, slot_factory(slots, running))
    router = SimpleNamespace(peers=peers) if peers is not None else None
    kw.setdefault("grace_s", GRACE)
    kw.setdefault("ready_timeout_s", READY_TIMEOUT)
    lease = LeaseManager(node, kind, supervisor, TABLE, router=router, emit=events, clock=clock, sleep=clock.sleep, **kw)
    return lease, slots, events, clock


def superior(**kw: bool) -> list[dict]:
    return [{"node": "node-b", "rank": [1, 10, "node-b"], "running": False, "claiming": False, "ready": False, **kw}]


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
    ready, fenced, claiming = (params.pop(key, False) for key in ("ready", "fenced", "claiming"))
    lease, *_ = build(*texts, **params)
    if ready:
        lease.set_ready("svc-s", True)
    if fenced:
        lease.note_fenced("svc-s")
    if claiming:
        lease._claiming.add("svc-s")
    assert lease.local_state() == json.loads(FIXTURE.read_text(encoding="utf-8"))[case]


def test_rank_order_and_non_singletons_skipped() -> None:
    active = rank(parse("svc-s"), KIND_WORKSTATION, "node-b")
    assert active == (1, 10, "node-b")
    assert outranks(active, rank(parse("svc-s:standby:prio=70"), KIND_WORKSTATION, "node-a"))
    assert outranks((0, 5, "node-a"), (0, 5, "node-b"))
    assert record_rank({"node": "node-c"}) == (0, 0, "node-c")
    assert list(build("svc", "svc-s")[0].local_state()) == ["svc-s"]


@pytest.mark.parametrize(
    ("text", "waited", "expect_start"),
    [("svc-s", 0.0, True), ("svc-s:standby", GRACE - 1, False), ("svc-s:standby", GRACE, True)],
)
async def test_free_slot_taken_by_role(text: str, waited: float, expect_start: bool) -> None:
    lease, slots, events, clock = build(text)
    await lease._decide(slots["svc-s"], [])
    clock.now += waited
    await lease._decide(slots["svc-s"], [])
    assert (slots["svc-s"].trace == ["start"]) is expect_start
    assert events.events == ([(EVENT_SINGLETON_ACTIVATED, {"slot": "svc-s", "node": "node-a"})] if expect_start else [])


@pytest.mark.parametrize(
    ("alive", "peer", "yields"),
    [(True, {"running": True}, True), (False, {"running": True}, False), (True, {"claiming": True}, True)],
)
async def test_superior_holds_the_slot(alive: bool, peer: dict, yields: bool) -> None:
    lease, slots, events, _ = build("svc-s:standby", running=("svc-s",))
    slots["svc-s"].process_alive = alive
    await lease._decide(slots["svc-s"], superior(**peer))
    assert slots["svc-s"].trace == (["stop"] if yields else [])
    assert events.events == ([(EVENT_SINGLETON_YIELDED, {"slot": "svc-s", "node": "node-a"})] if yields else [])


async def test_running_inferior_makes_claim_and_silent_superior_blocks_takeover() -> None:
    lease, slots, _, clock = build("svc-s")
    await lease._decide(slots["svc-s"], [{**superior(running=True)[0], "rank": [0, 10, "node-b"]}])
    assert slots["svc-s"].trace == [] and lease.local_state()["svc-s"]["claiming"] is True
    lease, slots, _, clock = build("svc-s:standby")
    for _ in range(2):
        await lease._decide(slots["svc-s"], superior())
        clock.now += GRACE * 2
    assert slots["svc-s"].trace == []


@pytest.mark.parametrize(
    ("ready", "elapsed", "expect"),
    [(False, READY_TIMEOUT - 1, []), (False, READY_TIMEOUT, ["start"]), (True, READY_TIMEOUT * 3, [])],
)
async def test_yielded_reclaims_when_superior_not_ready(ready: bool, elapsed: float, expect: list) -> None:
    lease, slots, events, clock = build("svc-s:standby", running=("svc-s",))
    peers = superior(running=True, ready=ready)
    await lease._decide(slots["svc-s"], peers)
    slots["svc-s"].trace.clear()
    events.events.clear()
    clock.now += elapsed
    await lease._decide(slots["svc-s"], peers)
    assert slots["svc-s"].trace == expect
    reason = {"slot": "svc-s", "node": "node-a", "reason": REASON_SUPERIOR_NOT_READY}
    assert events.events == ([(EVENT_SINGLETON_ACTIVATED, reason)] if expect else [])


async def test_fenced_stays_silent_for_holdoff() -> None:
    lease, slots, events, clock = build("svc-s")
    lease.note_fenced("svc-s")
    clock.now += FENCED_HOLDOFF_S - 1
    await lease._decide(slots["svc-s"], [])
    assert slots["svc-s"].trace == []
    clock.now += 1
    await lease._decide(slots["svc-s"], [])
    assert slots["svc-s"].trace == ["start"]
    assert events.events[0][0] == EVENT_SINGLETON_ACTIVATED


async def test_stuck_warning_once(caplog: pytest.LogCaptureFixture) -> None:
    lease, slots, _, clock = build("svc-s:standby", stuck_warn_s=50.0)
    caplog.set_level("WARNING")
    for _ in range(3):
        await lease._decide(slots["svc-s"], superior())
        clock.now += 60
    assert sum("не работает уже" in r.message for r in caplog.records) == 1


async def test_sleeping_and_silent_peers_do_not_claim() -> None:
    sleeping = FakeLink(alive=False, down_since=1.0, reply=[])
    peers = {
        "node-b": sleeping,
        "node-c": FakeLink(reply=TimeoutError()),
        "node-d": FakeLink(reply=ProtoError(ERR_UNAVAILABLE, "нет")),
    }
    lease, slots, _, _ = build("svc-s", peers=peers, running=("svc-s",))
    assert await lease._peer_singletons() == {}
    assert sleeping.asked == 0
    await lease.tick()
    assert slots["svc-s"].trace == []
    assert lease.local_state()["svc-s"]["claiming"] is False


async def test_peer_state_collected_and_used() -> None:
    lease, slots, events, _ = build("svc-s:standby", peers={"node-b": FakeLink(reply={"svc-s": GOOD})}, running=("svc-s",))
    assert await lease._peer_singletons() == {"svc-s": [{**GOOD, "node": "node-b"}]}
    await lease.tick()
    assert slots["svc-s"].trace == ["stop"]
    assert events.events[0][0] == EVENT_SINGLETON_YIELDED


@pytest.mark.parametrize(
    "bad", [{"rank": ["x", 1, "node-b"]}, {"rank": 5}, {"rank": [1]}, {"rank": [None, None, None]}, "не словарь", None]
)
async def test_broken_peer_record_is_skipped(bad: Any, caplog: pytest.LogCaptureFixture) -> None:
    lease, *_ = build("svc-s", peers={"node-b": FakeLink(reply={"svc-s": bad, "svc-i@one": GOOD})})
    with caplog.at_level(logging.WARNING):
        assert await lease._peer_singletons() == {"svc-i@one": [{**GOOD, "node": "node-b"}]}
    assert "svc-s" in caplog.text
    await lease.tick()


async def test_tick_edge_cases() -> None:
    lease, *_ = build("svc-s", peers={"node-b": FakeLink(reply=[1, 2])})
    assert await lease._peer_singletons() == {}
    lease, slots, _, _ = build("svc-s", kind=KIND_SERVER)
    assert await lease._peer_singletons() == {}
    await lease.tick()
    assert slots["svc-s"].trace == ["start"]
    await build("svc")[0].tick()


async def test_settle_returns_when_all_known() -> None:
    lease, _, _, clock = build("svc-s", peers={"node-b": FakeLink(), "node-c": FakeLink(alive=False, down_since=5.0)})
    await lease._settle()
    assert clock.sleeps == []


async def test_settle_waits_for_unknown_link() -> None:
    link = FakeLink(alive=False)
    lease, _, _, clock = build("svc-s", peers={"node-b": link}, settle_s=10.0)
    original = clock.sleep

    async def sleep(delay: float) -> None:
        await original(delay)
        link.alive = len(clock.sleeps) >= 3

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
    lease, slots, _, _ = build("svc-s", kind=KIND_SERVER, peers={})
    await lease.start()
    assert slots["svc-s"].trace == ["start"]
    assert lease._task is not None
    await lease.stop()
    assert lease._task is None


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
    assert (calls, clock.sleeps) == (2, [3.0, 3.0])
    assert any("ошибка цикла" in r.message for r in caplog.records)
