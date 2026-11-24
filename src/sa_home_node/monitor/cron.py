from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SEARCH_YEARS = 9


@dataclass(frozen=True)
class CronSpec:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]


def _unsupported(expr: str) -> ValueError:
    return ValueError(f"неподдерживаемое cron-выражение: {expr}")


def _value(token: str, names: tuple[str, ...], base: int) -> int:
    lowered = token.lower()
    if lowered in names:
        return names.index(lowered) + base
    if not token.isascii() or not token.isdigit():
        raise ValueError(token)
    return int(token)


def _parse_field(text: str, low: int, high: int, names: tuple[str, ...] = ()) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        body, sep, step_text = part.partition("/")
        step = 1
        if sep:
            if not step_text.isascii() or not step_text.isdigit() or int(step_text) < 1:
                raise ValueError(part)
            step = int(step_text)
        if body == "*":
            start, end = low, high
        elif "-" in body:
            first, _, last = body.partition("-")
            start, end = _value(first, names, low), _value(last, names, low)
        else:
            start = _value(body, names, low)
            end = high if sep else start
        if start < low or end > high or start > end:
            raise ValueError(part)
        values.update(range(start, end + 1, step))
    return frozenset(values)


def parse_cron(expr: str) -> CronSpec:
    parts = expr.split()
    if len(parts) != 5:
        raise _unsupported(expr)
    minute, hour, day, month, weekday = parts
    try:
        return CronSpec(
            minutes=_parse_field(minute, 0, 59),
            hours=_parse_field(hour, 0, 23),
            days=_parse_field(day, 1, 31),
            months=_parse_field(month, 1, 12, MONTH_NAMES),
            weekdays=_parse_field(weekday, 0, 6, DAY_NAMES),
        )
    except ValueError:
        raise _unsupported(expr) from None


def next_fire(spec: CronSpec, after: datetime) -> datetime:
    after = after.astimezone(UTC) if after.tzinfo else after.replace(tzinfo=UTC)
    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.date()
    last_day = day.replace(year=day.year + SEARCH_YEARS)
    while day <= last_day:
        if day.month in spec.months and day.day in spec.days and day.weekday() in spec.weekdays:
            for hour in sorted(spec.hours):
                for minute in sorted(spec.minutes):
                    candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
                    if candidate >= start:
                        return candidate
        day += timedelta(days=1)
    raise ValueError("cron-выражение никогда не сработает")


@dataclass
class _Entry:
    name: str
    spec: CronSpec
    callback: Callable[[], Awaitable[None]]
    loop: asyncio.Task | None = None
    running: asyncio.Task | None = field(default=None)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CronScheduler:
    def __init__(
        self,
        now: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    ) -> None:
        self._now = now
        self._sleep = sleep
        self._entries: dict[str, _Entry] = {}
        self._started = False

    def add(self, name: str, expr: str, callback: Callable[[], Awaitable[None]]) -> None:
        entry = _Entry(name, parse_cron(expr), callback)
        self._entries[name] = entry
        if self._started:
            entry.loop = asyncio.create_task(self._run(entry), name=f"cron:{name}")

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for entry in self._entries.values():
            entry.loop = asyncio.create_task(self._run(entry), name=f"cron:{entry.name}")

    async def stop(self) -> None:
        self._started = False
        tasks = []
        for entry in self._entries.values():
            tasks.extend(task for task in (entry.loop, entry.running) if task is not None)
            entry.loop = None
            entry.running = None
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self, entry: _Entry) -> None:
        while True:
            now = self._now()
            target = next_fire(entry.spec, now)
            await self._sleep(max((target - now).total_seconds(), 0.0))
            if entry.running is not None and not entry.running.done():
                log.warning("Задание %s ещё выполняется, срабатывание пропущено", entry.name)
                continue
            entry.running = asyncio.create_task(self._call(entry), name=f"cron-job:{entry.name}")

    async def _call(self, entry: _Entry) -> None:
        try:
            await entry.callback()
        except Exception:
            log.exception("Сбой задания %s", entry.name)
