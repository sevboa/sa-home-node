from __future__ import annotations

import asyncio
import inspect
import logging
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import aiohttp
import certifi
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramNetworkError
from aiohttp import TCPConnector
from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError

log = logging.getLogger(__name__)


DIRECT = "direct"
TICK_S = 300.0
BAN_S = 15 * 60.0
DIRECT_RETURN_STREAK = 2
SWITCH_GAIN_FACTOR = 2.0
SWITCH_GAIN_MS = 300
TROUBLE_CONSECUTIVE = 3
TROUBLE_NO_SUCCESS_S = 60.0
DIRECT_PROBE_TIMEOUT_S = 8.0


@dataclass(frozen=True)
class Candidate:
    route: str
    label: str
    ms: int | None
    valid: bool


@dataclass(frozen=True)
class Decision:
    route: str
    reason: str
    changed: bool


def socks_url(socks: str) -> str:
    return socks if "://" in socks else f"socks5://{socks}"


def normalize_candidates(raw: list[dict[str, Any]], extra_proxies: list[str]) -> list[Candidate]:
    out: list[Candidate] = []
    seen: set[str] = set()
    for item in raw:
        socks = item.get("socks")
        if not socks:
            continue
        route = socks_url(str(socks))
        check = item.get("check") or None
        ms = None
        valid = False
        if check:
            ms_raw = check.get("ms")
            ms = int(ms_raw) if isinstance(ms_raw, (int, float)) else None
            valid = bool(check.get("ok")) and not check.get("stale") and ms is not None
        label = str(item.get("label") or item.get("node") or route)
        seen.add(route)
        out.append(Candidate(route, label, ms, valid))
    for extra in extra_proxies:
        route = socks_url(extra)
        if route not in seen and extra:
            seen.add(route)
            out.append(Candidate(route, route, None, True))
    out.sort(key=lambda c: (not c.valid, c.ms is None, c.ms if c.ms is not None else 0))
    return out


def choose_route(
    *,
    current: str,
    direct_ok: bool,
    direct_streak: int,
    candidates: list[dict[str, Any]],
    bans: dict[str, float],
    now: float,
    prefer_direct: bool = True,
    extra_proxies: list[str] | None = None,
) -> Decision:
    cands = normalize_candidates(candidates, extra_proxies or [])
    usable = [c for c in cands if c.valid and bans.get(c.route, 0.0) <= now]
    by_route = {c.route: c for c in usable}

    if current == DIRECT:
        direct_usable = direct_ok
    else:
        direct_usable = direct_ok and direct_streak >= DIRECT_RETURN_STREAK

    if direct_usable and (prefer_direct or current == DIRECT):
        if current == DIRECT:
            return Decision(DIRECT, "direct жив", False)
        return Decision(DIRECT, "direct снова стабильно жив", True)

    cur = by_route.get(current)
    best = usable[0] if usable else None
    if current != DIRECT and cur is not None:
        if (
            best is not None
            and best.route != cur.route
            and best.ms is not None
            and cur.ms is not None
            and cur.ms > best.ms * SWITCH_GAIN_FACTOR
            and cur.ms - best.ms > SWITCH_GAIN_MS
        ):
            return Decision(best.route, f"выигрыш {cur.ms}→{best.ms} мс", True)
        return Decision(current, "текущий прокси годен", False)

    if best is not None:
        why = "direct недоступен" if current == DIRECT else "текущий прокси негоден"
        if current == DIRECT and direct_ok and not prefer_direct:
            return Decision(DIRECT, "direct жив", False)
        return Decision(best.route, why, True)
    if direct_usable:
        if current == DIRECT:
            return Decision(DIRECT, "direct жив", False)
        return Decision(DIRECT, "кандидатов нет, direct жив", True)
    return Decision(current, "кандидатов нет", False)


def apply_route(session: AiohttpSession, route: str) -> None:
    if route == DIRECT:
        session._connector_type = TCPConnector
        session._connector_init = {
            "ssl": ssl.create_default_context(cafile=certifi.where()),
            "limit": 100,
            "ttl_dns_cache": 3600,
        }
        session._proxy = None
        session._should_reset_connector = True
    else:
        session.proxy = route


def current_route(session: AiohttpSession) -> str:
    proxy = session.proxy
    return DIRECT if not proxy else str(proxy)


_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    TelegramNetworkError,
    aiohttp.ClientError,
    asyncio.TimeoutError,
    ProxyError,
    ProxyTimeoutError,
    ProxyConnectionError,
)


class TroubleDetector(BaseRequestMiddleware):
    def __init__(
        self,
        on_trouble: Callable[[], Any] | None = None,
        on_recovered: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.on_trouble = on_trouble
        self.on_recovered = on_recovered
        self._clock = clock
        self.failures = 0
        self.last_success = clock()
        self.troubled = False
        self._awaiting_recovery = False

    def reset(self) -> None:
        self.failures = 0
        self.last_success = self._clock()
        self.troubled = False

    def record_success(self) -> None:
        self.failures = 0
        self.last_success = self._clock()
        self.troubled = False
        if self._awaiting_recovery:
            self._awaiting_recovery = False
            _fire_callback(self.on_recovered)

    def record_failure(self) -> None:
        self.failures += 1
        if self.troubled:
            return
        now = self._clock()
        if self.failures >= TROUBLE_CONSECUTIVE or now - self.last_success >= TROUBLE_NO_SUCCESS_S:
            self.troubled = True
            self._awaiting_recovery = True
            _fire_callback(self.on_trouble)

    async def __call__(self, make_request, bot, method):  # type: ignore[override]
        try:
            result = await make_request(bot, method)
        except _NETWORK_ERRORS:
            self.record_failure()
            raise
        self.record_success()
        return result


_callback_tasks: set[asyncio.Task[Any]] = set()


def _fire_callback(cb: Callable[[], Any] | None) -> None:
    if cb is None:
        return
    try:
        res = cb()
    except Exception:
        log.exception("egress: колбэк упал")
        return
    if inspect.isawaitable(res):
        task = asyncio.ensure_future(res)
        _callback_tasks.add(task)
        task.add_done_callback(_callback_done)


def _callback_done(task: asyncio.Task[Any]) -> None:
    _callback_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        log.error("egress: колбэк упал", exc_info=task.exception())
