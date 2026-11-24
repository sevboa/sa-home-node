from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from sa_home_node.monitor.cron import CronScheduler, next_fire, parse_cron


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("expr", "after", "expected"),
    [
        ("*/1 * * * *", utc(2026, 1, 1, 10, 0, 0), utc(2026, 1, 1, 10, 1)),
        ("*/1 * * * *", utc(2026, 1, 1, 10, 0, 30), utc(2026, 1, 1, 10, 1)),
        ("0 * * * *", utc(2026, 1, 1, 10, 0), utc(2026, 1, 1, 11, 0)),
        ("0 * * * *", utc(2026, 1, 1, 10, 59), utc(2026, 1, 1, 11, 0)),
        ("0 3 * * *", utc(2026, 1, 1, 3, 0), utc(2026, 1, 2, 3, 0)),
        ("0 3 * * *", utc(2026, 1, 1, 2, 59, 59), utc(2026, 1, 1, 3, 0)),
        ("0 * * * *", utc(2026, 12, 31, 23, 30), utc(2027, 1, 1, 0, 0)),
        ("0 0 1 * *", utc(2026, 1, 31, 12, 0), utc(2026, 2, 1, 0, 0)),
        ("0 0 1 1 *", utc(2026, 6, 1, 0, 0), utc(2027, 1, 1, 0, 0)),
        ("0 0 29 2 *", utc(2026, 3, 1, 0, 0), utc(2028, 2, 29, 0, 0)),
        ("5,35 * * * *", utc(2026, 1, 1, 10, 6), utc(2026, 1, 1, 10, 35)),
        ("5,35 * * * *", utc(2026, 1, 1, 10, 36), utc(2026, 1, 1, 11, 5)),
        ("10-12 8 * * *", utc(2026, 1, 1, 8, 12), utc(2026, 1, 2, 8, 10)),
        ("*/20 9-10 * * *", utc(2026, 1, 1, 9, 40), utc(2026, 1, 1, 10, 0)),
        ("0-30/15 * * * *", utc(2026, 1, 1, 10, 15), utc(2026, 1, 1, 10, 30)),
        ("5/20 * * * *", utc(2026, 1, 1, 10, 6), utc(2026, 1, 1, 10, 25)),
        ("0 12 * * 0", utc(2026, 1, 1, 0, 0), utc(2026, 1, 5, 12, 0)),
        ("0 12 * * mon", utc(2026, 1, 5, 12, 0), utc(2026, 1, 12, 12, 0)),
        ("0 12 * * sat,sun", utc(2026, 1, 1, 0, 0), utc(2026, 1, 3, 12, 0)),
        ("0 12 * * mon-fri", utc(2026, 1, 2, 12, 0), utc(2026, 1, 5, 12, 0)),
        ("0 0 1 mar-may *", utc(2026, 1, 1, 0, 0), utc(2026, 3, 1, 0, 0)),
        ("0 0 1 DEC *", utc(2026, 1, 1, 0, 0), utc(2026, 12, 1, 0, 0)),
        ("0 0 13 * 4", utc(2026, 1, 1, 0, 0), utc(2026, 2, 13, 0, 0)),
    ],
)
def test_next_fire(expr, after, expected):
    assert next_fire(parse_cron(expr), after) == expected


def test_next_fire_is_strictly_after():
    spec = parse_cron("*/1 * * * *")
    moment = utc(2026, 1, 1, 0, 0)
    for _ in range(5):
        following = next_fire(spec, moment)
        assert following > moment
        moment = following


def test_naive_datetime_is_utc():
    assert next_fire(parse_cron("0 3 * * *"), datetime(2026, 1, 1, 0, 0)) == utc(2026, 1, 1, 3, 0)


@pytest.mark.parametrize(
    "expr",
    [
        "",
        "* * * *",
        "* * * * * *",
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * 32 * *",
        "* * * 13 *",
        "* * * * 7",
        "*/0 * * * *",
        "*/x * * * *",
        "5-1 * * * *",
        "a * * * *",
        "* * * foo *",
        "* * * * funday",
        "1,,2 * * * *",
        "-1 * * * *",
        "@daily",
    ],
)
def test_parse_rejects(expr):
    with pytest.raises(ValueError, match="неподдерживаемое cron-выражение"):
        parse_cron(expr)


def test_never_firing_expression():
    with pytest.raises(ValueError, match="никогда"):
        next_fire(parse_cron("0 0 31 2 *"), utc(2026, 1, 1))


class Clock:
    def __init__(self, start: datetime) -> None:
        self.current = start
        self.waiters: list[tuple[datetime, asyncio.Future]] = []

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((self.current + timedelta(seconds=seconds), future))
        await future

    async def advance_to(self, moment: datetime) -> None:
        while True:
            await settle()
            due = sorted((w for w in self.waiters if w[0] <= moment), key=lambda w: w[0])
            if not due:
                break
            wake, future = due[0]
            self.waiters.remove((wake, future))
            self.current = max(self.current, wake)
            future.set_result(None)
        self.current = moment
        await settle()


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


async def test_fires_on_schedule():
    clock = Clock(utc(2026, 1, 1, 0, 0, 20))
    fired: list[datetime] = []

    async def job() -> None:
        fired.append(clock.now())

    scheduler = CronScheduler(now=clock.now, sleep=clock.sleep)
    scheduler.add("job", "*/2 * * * *", job)
    await scheduler.start()
    await settle()
    assert clock.waiters[0][0] == utc(2026, 1, 1, 0, 2)
    await clock.advance_to(utc(2026, 1, 1, 0, 5))
    await scheduler.stop()
    assert fired == [utc(2026, 1, 1, 0, 2), utc(2026, 1, 1, 0, 4)]


async def test_overlap_is_skipped():
    clock = Clock(utc(2026, 1, 1, 0, 0))
    gate = asyncio.Event()
    calls: list[datetime] = []

    async def job() -> None:
        calls.append(clock.now())
        await gate.wait()

    scheduler = CronScheduler(now=clock.now, sleep=clock.sleep)
    scheduler.add("job", "*/1 * * * *", job)
    await scheduler.start()
    await clock.advance_to(utc(2026, 1, 1, 0, 5))
    assert calls == [utc(2026, 1, 1, 0, 1)]
    gate.set()
    await settle()
    await clock.advance_to(utc(2026, 1, 1, 0, 6))
    await scheduler.stop()
    assert calls == [utc(2026, 1, 1, 0, 1), utc(2026, 1, 1, 0, 6)]


async def test_missed_fires_coalesce():
    clock = Clock(utc(2026, 1, 1, 0, 0))
    calls: list[datetime] = []

    async def job() -> None:
        calls.append(clock.now())

    scheduler = CronScheduler(now=clock.now, sleep=clock.sleep)
    scheduler.add("job", "*/1 * * * *", job)
    await scheduler.start()
    await settle()
    clock.current = utc(2026, 1, 1, 0, 10, 30)
    clock.waiters[0][1].set_result(None)
    clock.waiters.clear()
    await settle()
    assert calls == [utc(2026, 1, 1, 0, 10, 30)]
    await scheduler.stop()


async def test_job_error_does_not_stop_schedule():
    clock = Clock(utc(2026, 1, 1, 0, 0))
    calls: list[datetime] = []

    async def job() -> None:
        calls.append(clock.now())
        raise RuntimeError("сбой")

    scheduler = CronScheduler(now=clock.now, sleep=clock.sleep)
    scheduler.add("job", "*/1 * * * *", job)
    await scheduler.start()
    await clock.advance_to(utc(2026, 1, 1, 0, 3))
    await scheduler.stop()
    assert len(calls) == 3


async def test_add_after_start_and_independent_jobs():
    clock = Clock(utc(2026, 1, 1, 0, 0))
    fired: list[str] = []

    async def hourly() -> None:
        fired.append("hourly")

    async def half() -> None:
        fired.append("half")

    scheduler = CronScheduler(now=clock.now, sleep=clock.sleep)
    scheduler.add("hourly", "0 * * * *", hourly)
    await scheduler.start()
    scheduler.add("half", "30 0 * * *", half)
    await clock.advance_to(utc(2026, 1, 1, 1, 0))
    await scheduler.stop()
    assert fired == ["half", "hourly"]


def test_add_rejects_bad_expression():
    scheduler = CronScheduler()
    with pytest.raises(ValueError, match="неподдерживаемое"):
        scheduler.add("job", "bad", lambda: asyncio.sleep(0))
