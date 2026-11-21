import asyncio
import sys

import pytest
from support import FakeSlot, slot_factory, spec, table

from sa_home_node.services.assignments import AssignmentError
from sa_home_node.services.supervisor import Supervisor, process_argv

TABLE = table(spec("svc"), spec("svc-s", singleton=True), spec("svc-i", instanced=True), spec("svc-ext", external=True))


def make(*texts: str) -> tuple[Supervisor, dict[str, FakeSlot]]:
    slots: dict[str, FakeSlot] = {}
    return Supervisor(texts or ("svc",), TABLE, slot_factory(slots)), slots


def test_parse_order_and_idempotent_assign() -> None:
    sup, _ = make("svc", "svc-s", "svc-i@one:standby", "svc-ext", "nope", "svc:bogus")
    assert list(sup.services) == ["svc", "svc-s", "svc-i@one"]
    assert sup.assign("svc-i@two") is sup.assign("svc-i@two:standby")


async def test_start_stop_order() -> None:
    sup, slots = make()
    sup.assign("svc-s")
    sup.assign("svc-i@one")
    await sup.start_all()
    assert [s.trace for s in slots.values()] == [["start"], [], ["start"]]
    order: list[str] = []
    for name, slot in slots.items():
        slot.stop = lambda name=name: order.append(name) or asyncio.sleep(0)
    await sup.stop_all()
    assert order == ["svc-i@one", "svc-s", "svc"]


class BrokenSlot(FakeSlot):
    async def stop(self) -> None:
        raise RuntimeError("boom")


class HangSlot(FakeSlot):
    cancelled = False

    async def stop(self) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def test_stop_all_silences_errors() -> None:
    sup, slots = make()
    sup.services["svc-i@one"] = BrokenSlot("svc-i@one")
    await sup.stop_all()
    assert slots["svc"].trace == ["stop"]


async def test_stop_all_is_concurrent_and_survives_cancel() -> None:
    sup, slots = make()
    hung = sup.services["svc-i@one"] = HangSlot("svc-i@one")
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(sup.stop_all(), 0.05)
    assert slots["svc"].trace == ["stop"]
    assert hung.cancelled


def test_assign_errors() -> None:
    sup, _ = make()
    for name in ("nope", "svc-ext"):
        with pytest.raises(ValueError, match=f"^неизвестное назначение: '{name}'$"):
            sup.assign(name)
    with pytest.raises(AssignmentError):
        sup.assign("svc:bogus")


async def test_unassign() -> None:
    sup, slots = make()
    await sup.unassign("svc")
    assert sup.get("svc") is None
    assert slots["svc"].trace == ["stop"]
    await sup.unassign("нет")


@pytest.mark.parametrize(
    ("instance", "config_path", "tail"),
    [("", None, []), ("one", None, ["--instance=one"]), ("-x", None, ["--instance=-x"]), ("", "/tmp/c.toml", ["--config", "/tmp/c.toml"])],
)
def test_process_argv(instance: str, config_path: str | None, tail: list) -> None:
    assert process_argv("svc", instance, config_path) == [sys.executable, "-m", "sa_home_node", "service", "svc", *tail]
