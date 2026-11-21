import asyncio
import logging
from types import SimpleNamespace

from sa_home_node.lifecycle import shutdown
from sa_home_node.lifecycle.shutdown import HARD_EXIT_CODE, ShutdownBudget, arm_hard_exit


async def fast() -> None:
    return None


async def slow() -> None:
    await asyncio.sleep(10)


async def broken() -> None:
    raise RuntimeError("сломалось")


async def test_step_fits_and_failure_is_contained(caplog):
    budget = ShutdownBudget(5.0)
    assert await budget.step("быстрый", fast()) is True
    assert await budget.step("упавший", broken()) is False
    assert budget.overdue == []
    assert "упавший" in caplog.text


async def test_step_overdue_dumps_once(caplog):
    budget = ShutdownBudget(5.0)
    with caplog.at_level(logging.WARNING):
        assert await budget.step("медленный", slow(), timeout=0.1) is False
        assert await budget.step("второй", slow(), timeout=0.1) is False
    assert budget.overdue == ["медленный", "второй"]
    assert sum("незавершённых задач" in r.message for r in caplog.records) == 1


async def test_step_cap_by_remaining_budget():
    budget = ShutdownBudget(0.0)
    assert await budget.step("поздний", slow(), timeout=50.0) is False
    assert budget.overdue == ["поздний"]


def test_arm_hard_exit(monkeypatch):
    slept, exits = [], []

    class InlineThread:
        def __init__(self, target, name, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(shutdown, "threading", SimpleNamespace(Thread=InlineThread))
    monkeypatch.setattr(shutdown, "time", SimpleNamespace(sleep=slept.append))
    monkeypatch.setattr(shutdown, "os", SimpleNamespace(_exit=exits.append))
    monkeypatch.setattr(shutdown.logging, "shutdown", lambda: None)
    arm_hard_exit(7.0)
    assert (slept, exits) == ([7.0], [HARD_EXIT_CODE])
