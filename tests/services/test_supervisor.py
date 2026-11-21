import asyncio
import sys
from typing import Any

import pytest

from sa_home_node.plugins import Plugin, ServiceSpec
from sa_home_node.services.assignments import AssignmentError
from sa_home_node.services.registry import ServiceTable
from sa_home_node.services.supervisor import Supervisor, process_argv


def spec(name: str, **kwargs: object) -> ServiceSpec:
    async def run(ctx: object) -> None:
        pass

    return ServiceSpec(name=name, run=run, **kwargs)


def plugin() -> Plugin:
    return Plugin(
        name="p",
        services=(
            spec("svc"),
            spec("svc-s", singleton=True),
            spec("svc-i", instanced=True),
            spec("svc-ext", external=True),
        ),
    )


def table() -> ServiceTable:
    return ServiceTable((plugin(),))


class FakeSlot:
    def __init__(self, trace: list, spec: ServiceSpec, assignment: Any) -> None:
        self.trace = trace
        self.spec = spec
        self.assignment = assignment
        self.name = assignment.key

    async def start(self) -> None:
        self.trace.append(("start", self.name))

    async def stop(self) -> None:
        self.trace.append(("stop", self.name))

    async def restart(self) -> None:
        self.trace.append(("restart", self.name))

    def to_dict(self) -> dict:
        return {"name": self.name}


def make(trace: list) -> Supervisor:
    return Supervisor(["svc"], table(), lambda s, a: FakeSlot(trace, s, a))


def test_parse_order() -> None:
    trace: list = []
    sup = Supervisor(
        ["svc", "svc-s", "svc-i@one:standby", "svc-ext", "nope", "svc:bogus"],
        table(),
        lambda s, a: FakeSlot(trace, s, a),
    )
    assert list(sup.services) == ["svc", "svc-s", "svc-i@one"]


async def test_start_stop_order() -> None:
    trace: list = []
    sup = make(trace)
    sup.assign("svc-s")
    sup.assign("svc-i@one")
    await sup.start_all()
    assert trace == [("start", "svc"), ("start", "svc-i@one")]
    trace.clear()
    await sup.stop_all()
    assert trace == [("stop", "svc-i@one"), ("stop", "svc-s"), ("stop", "svc")]


async def test_stop_all_silences_error() -> None:
    trace: list = []
    sup = make(trace)
    sup.assign("svc-i@one")
    broken = sup.services["svc-i@one"]
    sup.services["svc-i@one"] = BrokenSlot(trace, broken.spec, broken.assignment)
    await sup.stop_all()
    assert trace == [("stop", "svc")]


class BrokenSlot(FakeSlot):
    async def stop(self) -> None:
        raise RuntimeError("boom")


async def test_stop_all_is_concurrent_and_survives_cancel() -> None:
    trace: list = []
    sup = make(trace)
    sup.assign("svc-i@one")
    hung = HangSlot(trace, sup.services["svc-i@one"].spec, sup.services["svc-i@one"].assignment)
    sup.services["svc-i@one"] = hung
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(sup.stop_all(), 0.05)
    assert ("stop", "svc") in trace
    assert hung.cancelled


class HangSlot(FakeSlot):
    cancelled = False

    async def stop(self) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def test_assign_idempotent() -> None:
    trace: list = []
    sup = make(trace)
    first = sup.assign("svc-i@two")
    second = sup.assign("svc-i@two:standby")
    assert first is second


def test_assign_errors() -> None:
    trace: list = []
    sup = make(trace)
    with pytest.raises(ValueError) as a:
        sup.assign("nope")
    assert str(a.value) == "неизвестное назначение: 'nope'"
    with pytest.raises(ValueError) as b:
        sup.assign("svc-ext")
    assert str(b.value) == "неизвестное назначение: 'svc-ext'"
    with pytest.raises(AssignmentError):
        sup.assign("svc:bogus")


async def test_unassign() -> None:
    trace: list = []
    sup = make(trace)
    await sup.unassign("svc")
    assert sup.get("svc") is None
    assert trace == [("stop", "svc")]
    await sup.unassign("нет")


@pytest.mark.parametrize(
    ("instance", "config_path", "tail"),
    [("", None, []), ("one", None, ["--instance=one"]), ("-x", None, ["--instance=-x"]), ("", "/tmp/c.toml", ["--config", "/tmp/c.toml"])],
)
def test_process_argv(instance: str, config_path: str | None, tail: list) -> None:
    argv = process_argv("svc", instance, config_path)
    assert argv[0] == sys.executable
    assert argv[1:] == ["-m", "sa_home_node", "service", "svc"] + tail
