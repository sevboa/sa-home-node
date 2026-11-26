from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sa_home_node.bot.db import AppState

MIN_INTERVAL = timedelta(seconds=60)
MAX_PER_DAY = 5
WINDOW = timedelta(days=1)
PREFIX = "action_ticks:"

_TOO_OFTEN = "⏳ Слишком часто — подождите ~{wait} с."
_EXHAUSTED = "🚫 Лимит {limit} ручных сканов за сутки исчерпан. Следующий — через ~{mins} мин."


@dataclass(frozen=True)
class ScanDecision:
    allowed: bool
    reason: str
    ticks: tuple[datetime, ...]


def _mins(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60) + 1


def decide(ticks: list[datetime], now: datetime) -> ScanDecision:
    recent = sorted(t for t in ticks if now - t < WINDOW)

    if recent and now - recent[-1] < MIN_INTERVAL:
        wait = int((MIN_INTERVAL - (now - recent[-1])).total_seconds()) + 1
        return ScanDecision(False, _TOO_OFTEN.format(wait=wait), tuple(recent))

    if len(recent) >= MAX_PER_DAY:
        free_in = _mins(recent[0] + WINDOW - now)
        return ScanDecision(
            False,
            _EXHAUSTED.format(limit=MAX_PER_DAY, mins=free_in),
            tuple(recent),
        )

    return ScanDecision(True, "", (*recent, now)[-MAX_PER_DAY:])


def _parse(value: str | None) -> list[datetime] | None:
    if value is None:
        return None
    try:
        raw = json.loads(value)
        return [datetime.fromisoformat(item) for item in raw]
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


async def get_action_ticks(state: AppState, key: str) -> list[datetime]:
    ticks = _parse(await state.get(PREFIX + key))
    return ticks or []


async def set_action_ticks(state: AppState, key: str, ticks: list[datetime]) -> None:
    await state.set(PREFIX + key, json.dumps([tick.isoformat() for tick in ticks]))
