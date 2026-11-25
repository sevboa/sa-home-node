from __future__ import annotations

import asyncio
import contextlib
import html
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.broadcast import notify_admins
from sa_home_node.bot.config import TelegramConfig
from sa_home_node.bot.egress import (
    BAN_S,
    DIRECT,
    DIRECT_RETURN_STREAK,
    TICK_S,
    Candidate,
    Decision,
    TroubleDetector,
    apply_route,
    choose_route,
    current_route,
    normalize_candidates,
    socks_url,
)
from sa_home_node.bot.notifier import Notifier
from sa_home_node.bot.outboxflush import OutboxFlusher
from sa_home_node.bot.retry import REQUEST_TIMEOUT_S

log = logging.getLogger(__name__)

EGRESS_STATE_PATH = Path("./data/egress.json")
NOTICE_MIN_INTERVAL_S = 10 * 60.0


def load_saved_state(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("route"), str):
        return None
    return data


def save_state(path: Path, data: dict[str, Any]) -> None:
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        log.warning("egress: не удалось сохранить состояние в %s", path, exc_info=True)


async def _await_callback(cb: Callable[..., Any] | None, *args: Any) -> None:
    if cb is None:
        return
    try:
        res = cb(*args)
    except Exception:
        log.exception("egress: колбэк упал")
        return
    if inspect.isawaitable(res):
        await res


def startup_routes(cfg: TelegramConfig, state_path: Path = EGRESS_STATE_PATH) -> list[str]:
    saved = load_saved_state(state_path)
    order = [
        saved["route"] if saved else "",
        socks_url(cfg.proxy) if cfg.proxy else DIRECT,
        DIRECT,
        *(socks_url(p) for p in cfg.extra_proxies if p),
    ]
    out: list[str] = []
    for route in order:
        if route and route not in out:
            out.append(route)
    return out


async def pick_startup_route(bot: Bot, routes: list[str]) -> str | None:
    for route in routes:
        apply_route(bot.session, route)
        try:
            await bot.get_me(request_timeout=REQUEST_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            log.warning("egress: на старте %s не отвечает: %s", route, exc)
            continue
        if route != routes[0]:
            log.warning("egress: на старте выбран %s", route)
        return route
    if routes:
        apply_route(bot.session, routes[0])
    return None


def _route_name(route: str, label: str | None = None) -> str:
    if route == DIRECT:
        return "напрямую"
    return f"через {html.escape(label or route)}"


def render_switch(old: str, new: str, reason: str, candidates: list[dict[str, Any]]) -> str:
    label = {c["route"]: c.get("label") for c in candidates}
    if new == DIRECT:
        head = "📡 Telegram снова доступен напрямую — вернулся с прокси."
    elif old == DIRECT:
        head = f"📡 Telegram недоступен напрямую → переключился {_route_name(new, label.get(new))}."
    else:
        head = f"📡 Сменил прокси до Telegram → {_route_name(new, label.get(new))}."
    lines = [head, f"Причина: {html.escape(reason)}"]
    rows: list[str] = []
    for c in candidates:
        if c["route"] == DIRECT:
            name = "direct"
        else:
            name = html.escape(str(c.get("label") or c["route"]))
        mark = "✅" if c.get("ok") else "✗"
        ms = f" {c['ms']} мс" if c.get("ms") is not None else ""
        rows.append(f"  {mark} {name}{ms}")
    if rows:
        lines += ["Кандидаты:", *rows]
    return "\n".join(lines)


class SwitchNotices:
    def __init__(
        self,
        book: SubscriptionBook,
        notifier: Notifier,
        flusher: OutboxFlusher,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._book = book
        self._notifier = notifier
        self._flusher = flusher
        self._clock = clock
        self._last_sent: float | None = None
        self._suppressed = 0

    async def on_switched(self, old: str, new: str, reason: str, candidates: list[dict[str, Any]]) -> None:
        self._flusher.kick()
        now = self._clock()
        if self._last_sent is not None and now - self._last_sent < NOTICE_MIN_INTERVAL_S:
            self._suppressed += 1
            log.info("egress: оповещение о переключении подавлено (антиспам)")
            return
        text = render_switch(old, new, reason, candidates)
        if self._suppressed:
            text += f"\n\nМаршрут менялся ещё {self._suppressed} раз(а) с прошлого оповещения."
        self._last_sent = now
        self._suppressed = 0
        await notify_admins(self._book, self._notifier, text)


class EgressManager:
    def __init__(
        self,
        session: AiohttpSession,
        config: TelegramConfig,
        fetch_candidates: Callable[[], Awaitable[list[dict[str, Any]]]],
        probe_direct: Callable[[], Awaitable[bool]],
        on_switched: Callable[..., Any] | None = None,
        state_path: Path = EGRESS_STATE_PATH,
        clock: Callable[[], float] = time.time,
        on_recovered: Callable[[], Any] | None = None,
    ) -> None:
        self.session = session
        self.cfg = config
        self._fetch = fetch_candidates
        self._state_path = Path(state_path)
        self._clock = clock
        self._probe_direct = probe_direct
        self.on_switched = on_switched
        self.on_recovered = on_recovered

        self.route = current_route(self.session)
        self.label = self.route
        self.ms: int | None = None
        self.since = clock()
        self.direct_ok = True
        self.direct_streak = 0
        self.bans: dict[str, float] = {}
        self.saved = load_saved_state(self._state_path)

        self._trouble = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self.detector = TroubleDetector(
            on_trouble=self._trouble.set,
            on_recovered=self.on_recovered,
            clock=clock,
        )

    def start(self) -> None:
        self.session.middleware(self.detector)
        self._task = asyncio.create_task(self._loop(), name="egress-manager")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _loop(self) -> None:
        trouble_wait: asyncio.Task | None = None
        while True:
            trouble_wait = asyncio.ensure_future(self._trouble.wait())
            try:
                await asyncio.wait_for(asyncio.shield(trouble_wait), timeout=TICK_S)
                self._trouble.clear()
                await self._guard(self.handle_trouble)
            except TimeoutError:
                trouble_wait.cancel()
                await self._guard(self.tick)
            except asyncio.CancelledError:
                trouble_wait.cancel()
                raise

    async def _guard(self, fn: Callable[[], Awaitable[None]]) -> None:
        try:
            await fn()
        except Exception:
            log.exception("egress: сбой в %s", getattr(fn, "__name__", fn))

    async def handle_trouble(self) -> None:
        async with self._lock:
            if self.route == DIRECT:
                self.direct_ok = False
                self.direct_streak = 0
            else:
                self.bans[self.route] = self._clock() + BAN_S
            await self._evaluate("сбои запросов")
            self.detector.reset()

    async def tick(self) -> None:
        async with self._lock:
            if self.route != DIRECT:
                if await self._probe_direct():
                    self.direct_streak += 1
                    self.direct_ok = True
                else:
                    self.direct_streak = 0
                    self.direct_ok = False
            else:
                idle = self._clock() - self.detector.last_success >= TICK_S
                self.direct_ok = not self.detector.troubled and (not idle or await self._probe_direct())
                self.direct_streak = DIRECT_RETURN_STREAK if self.direct_ok else 0
            await self._evaluate("плановая проверка")

    async def _evaluate(self, why: str) -> None:
        try:
            raw = await self._fetch()
        except Exception:
            log.warning("egress: не удалось получить кандидатов", exc_info=True)
            raw = []
        now = self._clock()
        dec = choose_route(
            current=self.route,
            direct_ok=self.direct_ok,
            direct_streak=self.direct_streak,
            candidates=raw,
            bans=self.bans,
            now=now,
            prefer_direct=self.cfg.prefer_direct,
            extra_proxies=self.cfg.extra_proxies,
        )
        cands = normalize_candidates(raw, self.cfg.extra_proxies)
        if dec.changed:
            await self._switch(dec, cands, why)
        else:
            self._refresh_meta(cands)

    def _refresh_meta(self, cands: list[Candidate]) -> None:
        for c in cands:
            if c.route == self.route:
                self.label, self.ms = c.label, c.ms

    async def _switch(self, dec: Decision, cands: list[Candidate], why: str) -> None:
        old = self.route
        apply_route(self.session, dec.route)
        self.route = dec.route
        self.since = self._clock()
        self.label, self.ms = dec.route, None
        self._refresh_meta(cands)
        self.detector.reset()
        self._persist()
        log.warning("egress: %s → %s (%s; %s)", old, dec.route, why, dec.reason)
        details = [
            {"route": DIRECT, "label": DIRECT, "ok": self.direct_ok, "ms": None},
            *[
                {
                    "route": c.route,
                    "label": c.label,
                    "ok": c.valid and self.bans.get(c.route, 0.0) <= self._clock(),
                    "ms": c.ms,
                }
                for c in cands
            ],
        ]
        await _await_callback(self.on_switched, old, dec.route, f"{why}: {dec.reason}", details)

    def _persist(self) -> None:
        save_state(self._state_path, self.state())

    def state(self) -> dict[str, Any]:
        return {"route": self.route, "label": self.label, "ms": self.ms, "since": self.since}
