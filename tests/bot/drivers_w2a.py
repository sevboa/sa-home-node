from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import aiohttp
import aiohttp_socks
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramNetworkError
from aiogram.methods import GetMe
from aiogram.types import Update
from botfix import inflate
from drivers import StubNotifier, Ticker
from fakebot import SIGNATURES, FakeBot, make_error

REAL_SLEEP = asyncio.sleep
LOCAL_ZONE = "Etc/GMT-5"


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text)


def script_of(case: dict) -> dict[str, list[BaseException | None]]:
    return {
        method: [
            None if token == "ok" else make_error(token, str(index + 1)) for index, token in enumerate(tokens)
        ]
        for method, tokens in (case.get("script") or {}).items()
    }


async def poll(check: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        await REAL_SLEEP(0.01)
    return check()


def rows_of(path: Path, sql: str) -> list[list[Any]]:
    connection = sqlite3.connect(path)
    try:
        return [list(row) for row in connection.execute(sql)]
    finally:
        connection.close()


class Records(logging.Handler):
    def __init__(self, keep: Callable[[str], bool]) -> None:
        super().__init__(level=logging.DEBUG)
        self.keep = keep
        self.rows: list[list[str]] = []
        self.previous = logging.NOTSET

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if self.keep(text):
            self.rows.append([record.levelname, text])

    def __enter__(self) -> Self:
        root = logging.getLogger()
        self.previous = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(self)
        return self

    def __exit__(self, *exc: object) -> None:
        root = logging.getLogger()
        root.removeHandler(self)
        root.setLevel(self.previous)


@contextmanager
def local_zone(name: str = LOCAL_ZONE):
    saved = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        time.tzset()


class W2Bot(FakeBot):
    def __init__(self) -> None:
        super().__init__()
        self.gate: asyncio.Event | None = None
        self.entered = asyncio.Event()

    def _record(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        if name in SIGNATURES:
            super()._record(name, args, kwargs)
        else:
            self.calls.append((name, dict(kwargs)))

    async def send_message(self, *args: Any, **kwargs: Any) -> Any:
        if self.gate is not None:
            self.entered.set()
            await self.gate.wait()
        return await super().send_message(*args, **kwargs)


def outbox_log(text: str) -> bool:
    return text.startswith("outbox:")


async def drive_outbox(case: dict, kit: Any, workdir: Path) -> dict:
    path = workdir / "outbox.sqlite"
    outbox, close = await kit.open(path)
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(float(delay))

    bot = W2Bot()
    bot.script = script_of(case)
    notifier = kit.notifier(bot)
    second = None
    try:
        for item in case["queue"]:
            await kit.enqueue(
                outbox,
                item["chat_id"],
                item["kind"],
                {key: inflate(value) for key, value in item["payload"].items()},
                utc(item["at"]),
                item.get("thread"),
            )
        now = utc(case["now"])
        with local_zone(), Records(outbox_log) as logs:
            if case.get("concurrent"):
                bot.gate = asyncio.Event()
                first = asyncio.ensure_future(kit.flush(notifier, outbox, now=now, sleep=sleep))
                await asyncio.wait_for(bot.entered.wait(), 5)
                second = await kit.flush(notifier, outbox, now=now, sleep=sleep)
                bot.gate.set()
                sent = await first
            else:
                sent = await kit.flush(notifier, outbox, now=now, sleep=sleep)
        remaining = rows_of(path, "SELECT id, chat_id, attempts FROM outbox ORDER BY id")
    finally:
        await close()
    result = {"sent": sent, "calls": bot.dump(), "sleeps": sleeps, "remaining": remaining, "logs": logs.rows}
    if case.get("concurrent"):
        result["second"] = second
    return result


async def drive_flusher(case: dict, kit: Any, workdir: Path) -> dict:
    path = workdir / "flusher.sqlite"
    outbox, close = await kit.open(path)
    ticker = Ticker()
    bot = W2Bot()
    bot.script = script_of(case)
    flusher = kit.Flusher(kit.notifier(bot), outbox)
    trace: list[int] = []
    restore: Callable[[], None] | None = None
    original = asyncio.sleep
    asyncio.sleep = ticker.sleep
    try:
        with Records(outbox_log) as logs:
            for step in case["steps"]:
                name = step[0]
                if name == "enqueue":
                    await kit.enqueue(outbox, step[1], "alert", {"text": step[2]}, datetime.now(UTC), None)
                elif name == "start":
                    flusher.start()
                    await poll(lambda: len(ticker.delays) >= 1)
                elif name == "tick":
                    before = len(ticker.delays)
                    ticker._gate.release()
                    await poll(lambda before=before: len(ticker.delays) > before)
                elif name == "kick":
                    flusher.kick()
                    if step[1]:
                        await poll(lambda need=step[1]: len(bot.of("send_message")) >= need)
                    await poll(lambda: False, 0.3)
                elif name == "break":
                    restore = kit.break_due(outbox)
                elif name == "unbreak" and restore is not None:
                    restore()
                elif name == "stop":
                    await flusher.stop()
                trace.append(len(bot.of("send_message")))
    finally:
        asyncio.sleep = original
        await flusher.stop()
        await close()
    remaining = rows_of(path, "SELECT chat_id, attempts FROM outbox ORDER BY id")
    return {"trace": trace, "delays": sorted(set(ticker.delays)), "remaining": remaining, "logs": logs.rows}


def sub_view(sub: Any) -> dict:
    stamp = sub.invited_at
    fresh = None
    if stamp:
        fresh = abs((datetime.now(UTC) - datetime.fromisoformat(stamp)).total_seconds()) < 120
    return {
        "name": sub.name,
        "chat_id": sub.chat_id,
        "source": sub.source,
        "event_types": sorted(sub.event_types),
        "except_event_types": sorted(sub.except_event_types),
        "allowed_commands": sorted(sub.allowed_commands),
        "broken": sub.broken,
        "invited_by_chat_id": sub.invited_by_chat_id,
        "invited_user": sub.invited_user,
        "invited_at_fresh": fresh,
    }


def code_text(spec: Any, issued: list[str]) -> str | None:
    if not isinstance(spec, dict):
        return spec
    code = issued[spec["ref"]]
    shape = spec.get("fmt", "plain")
    if shape == "printed":
        code = f"{code[:4]}-{code[4:]}"
    elif shape == "lower":
        code = f"{code[:4]}-{code[4:]}".lower()
    elif shape == "spaced":
        code = f" {code[:4]} {code[4:]}\n"
    elif shape == "confusable":
        code = code.replace("1", "I").replace("0", "O").lower()
    return spec.get("prefix", "") + code


GATE_SUBS = (("root", 1001, ["*"]), ("plain", 1002, ["status"]), ("second-root", 1003, ["*"]))


async def drive_gate(case: dict, kit: Any, workdir: Path) -> dict:
    store, close = await kit.open_invites(workdir / "gate.sqlite")
    book = kit.book([kit.sub(name, chat, rights) for name, chat, rights in GATE_SUBS])
    guest_path = workdir / "guests.toml" if case["guest_file"] else None
    notifier = StubNotifier([])
    gate = kit.Gatekeeper(kit.cfg(case["cfg"]), store, book, kit.guest_store(guest_path), notifier)
    issued: list[str] = []

    def ref(code: str) -> str:
        return f"@{issued.index(code)}" if code in issued else code

    trace: list[Any] = []
    try:
        for step in case["steps"]:
            name = step[0]
            if name == "issue":
                started = datetime.now(UTC)
                code, expires = await gate.issue(step[1], step[2], step[3])
                issued.append(code)
                trace.append(
                    {
                        "shape": bool(re.fullmatch(f"[{kit.alphabet}]{{8}}", code)),
                        "ttl": round((expires - started).total_seconds()),
                    }
                )
            elif name == "open":
                rows = await gate.open_codes()
                trace.append([[ref(r["code"]), r["issued_by_chat_id"], r["issued_by_user_id"]] for r in rows])
            elif name == "known":
                found = await gate.known_code(code_text(step[1], issued))
                if found is None:
                    trace.append(None)
                else:
                    code, row = found
                    trace.append(
                        [
                            ref(code),
                            {
                                "issued_by_chat_id": row["issued_by_chat_id"],
                                "issued_by_user_id": row["issued_by_user_id"],
                                "redeemed": row["redeemed_at"] is not None,
                                "revoked": row["revoked_at"] is not None,
                            },
                        ]
                    )
            elif name == "revoke":
                target = issued[step[1]] if isinstance(step[1], int) else step[1]
                trace.append(await gate.revoke_code(target))
            elif name == "admit":
                extra = step[3] if len(step) > 3 else {}
                admission = await gate.try_admit(step[1], code_text(step[2], issued), **extra)
                trace.append(
                    None
                    if admission is None
                    else {
                        "invited_by": admission.invited_by_chat_id,
                        "code": ref(admission.code),
                        "sub": sub_view(admission.subscription),
                    }
                )
            elif name == "enabled":
                trace.append(gate.enabled)
            elif name == "file":
                if guest_path is None or not guest_path.exists():
                    trace.append(None)
                else:
                    text = guest_path.read_text(encoding="utf-8")
                    for stamp in re.findall(r'invited_at = "([^"]*)"', text):
                        assert abs((datetime.now(UTC) - datetime.fromisoformat(stamp)).total_seconds()) < 120
                    trace.append(re.sub(r'invited_at = "[^"]*"', 'invited_at = "<now>"', text))
            elif name == "guests":
                trace.append([sub_view(sub) for sub in book.guests()])
            elif name == "sent":
                trace.append([list(row) for row in notifier.sent])
    finally:
        await close()
    return {"trace": trace}


async def drive_limiter(case: dict, limiter: Any) -> list[Any]:
    out: list[Any] = []
    for step in case["ops"]:
        name = step[0]
        if name == "register":
            out.append(limiter.register(step[1], step[2]))
        elif name == "exhausted":
            out.append(limiter.exhausted_now(step[1]))
        else:
            limiter.forget(step[1])
            out.append(None)
    return out


def drive_guests(case: dict, kit: Any) -> dict:
    saved: list[list[list[Any]]] = []

    class Counting:
        available = True

        def save(self, guests: Any) -> bool:
            saved.append(sorted([guest.chat_id, sorted(guest.allowed_commands)] for guest in guests))
            return True

    book = kit.book(
        [
            kit.sub(spec["name"], spec["chat_id"], spec["allowed"], source=spec["source"])
            for spec in case["subs"]
        ]
    )
    store = Counting()
    trace: list[Any] = []
    for op in case["ops"]:
        name, chat, *rest = op
        if name == "revoke":
            result: Any = kit.revoke(book, store, chat)
        elif name == "set":
            result = kit.set_rights(book, store, chat, frozenset(rest[0]))
        elif name == "add":
            result = kit.add_right(book, store, chat, rest[0])
        else:
            result = kit.remove_right(book, store, chat, rest[0])
        trace.append(
            {
                "result": sub_view(result) if hasattr(result, "allowed_commands") else result,
                "saves": len(saved),
                "last_save": saved[-1] if saved else None,
                "book": sorted([sub.chat_id, sorted(sub.allowed_commands)] for sub in book.all()),
            }
        )
    return {"trace": trace}


def user_dict(spec: dict) -> dict:
    return {"is_bot": False, **{key: value for key, value in spec.items() if value is not None}}


def message_dict(spec: dict) -> dict:
    body: dict[str, Any] = {
        "message_id": 7,
        "date": 1700000000,
        "chat": {"type": "private", **spec["chat"]},
    }
    if spec.get("user"):
        body["from"] = user_dict(spec["user"])
    if spec.get("text") is not None:
        body["text"] = spec["text"]
    return body


def build_update(spec: dict, bot: Any) -> Update:
    kind = spec["kind"]
    person = user_dict(spec.get("user") or {"id": 1, "first_name": "x"})
    if kind in ("message", "edited_message"):
        body = {kind: message_dict(spec)}
    elif kind == "callback":
        query = {"id": "cb1", "from": person, "chat_instance": "ci", "data": spec.get("data")}
        if spec.get("chat"):
            query["message"] = message_dict(spec)
        body = {"callback_query": query}
    elif kind == "my_chat_member":
        agent = {"id": 99, "is_bot": True, "first_name": "bot"}
        body = {
            "my_chat_member": {
                "chat": {"type": "private", **spec["chat"]},
                "from": person,
                "date": 1700000000,
                "old_chat_member": {"status": "left", "user": agent},
                "new_chat_member": {"status": "member", "user": agent},
            }
        }
    else:
        body = {"inline_query": {"id": "q", "from": person, "query": "x", "offset": ""}}
    return Update.model_validate({"update_id": 1, **body}, context={"bot": bot})


class StubGate:
    def __init__(self, admit: dict[str, str]) -> None:
        self.admit = admit
        self.attempts: list[list[Any]] = []

    async def try_admit(
        self,
        chat_id: int,
        text: str | None,
        *,
        user_id: int | None = None,
        user_name: str | None = None,
        chat_title: str | None = None,
    ) -> Any:
        self.attempts.append([chat_id, text, user_id, user_name, chat_title])
        code = self.admit.get(text) if text is not None else None
        return None if code is None else SimpleNamespace(code=code)


async def drive_middleware(case: dict, data: dict, kit: Any) -> dict:
    bot = W2Bot()
    book = kit.book(
        [
            kit.sub(spec["name"], spec["chat_id"], spec["allowed"], broken=spec["broken"])
            for spec in data["subs"]
            if spec["name"] in case["book"]
        ]
    )
    gate = StubGate(case.get("admit", {}))
    seen: list[dict] = []

    async def handler(event: Any, payload: dict) -> str:
        admission = payload.get("admission")
        subscription = payload.get("subscription")
        seen.append(
            {
                "admission": None if admission is None else admission.code,
                "subscription": None if subscription is None else subscription.name,
                "has_subscription_key": "subscription" in payload,
            }
        )
        return "handled"

    update = build_update(case["update"], bot)
    payload: dict[str, Any] = {}
    if case["mw"] == "silence":
        result = await kit.silence(book, gate)(handler, update, payload)
    elif case["mw"] == "auth":
        result = await kit.auth(book)(handler, update.message, payload)
    else:
        result = await kit.callback(book)(handler, update.callback_query, payload)
    return {"result": result, "seen": seen, "calls": bot.dump(), "attempts": gate.attempts}


async def drive_scan(case: dict, kit: Any, workdir: Path) -> dict:
    state, close = await kit.open(workdir / "scan.sqlite")
    trace: list[Any] = []
    try:
        for step in case["steps"]:
            name = step[0]
            if name == "try":
                key, now, accepted = step[1], utc(step[2]), step[3]
                decision = kit.decide(await kit.get(state, key), now)
                trace.append(
                    {
                        "allowed": decision.allowed,
                        "reason": decision.reason,
                        "ticks": [t.isoformat() for t in decision.ticks],
                    }
                )
                if decision.allowed and accepted:
                    await kit.put(state, key, list(decision.ticks))
            elif name == "raw":
                trace.append(await kit.raw(state, step[1]))
            elif name == "put_raw":
                await kit.put_raw(state, step[1], step[2])
                trace.append(None)
            else:
                trace.append([t.isoformat() for t in await kit.get(state, step[1])])
    finally:
        await close()
    return {"trace": trace}


def error_for(kind: str) -> BaseException | None:
    if kind == "net":
        return TelegramNetworkError(GetMe(), "net")
    if kind == "client":
        return aiohttp.ClientError("client")
    if kind == "timeout":
        return TimeoutError()
    if kind == "proxy":
        return aiohttp_socks.ProxyConnectionError("proxy")
    if kind == "proxy_timeout":
        return aiohttp_socks.ProxyTimeoutError("proxy")
    if kind == "bad":
        return TelegramBadRequest(GetMe(), "bad")
    return None


async def drive_trouble(case: dict, factory: Any) -> dict:
    now = [case.get("start", 0.0)]
    counts = {"trouble": 0, "recovered": 0}
    mode = case.get("callbacks", "sync")

    def make(key: str) -> Any:
        if mode == "none":
            return None

        async def later() -> None:
            counts[key] += 1

        def now_cb() -> None:
            counts[key] += 1
            if mode == "raises":
                raise RuntimeError("callback")

        return later if mode == "async" else now_cb

    detector = factory(make("trouble"), make("recovered"), lambda: now[0])
    outcomes: list[str] = []
    state_after: list[list[Any]] = []
    for step in case["steps"]:
        kind = step[0]
        if kind == "advance":
            now[0] += step[1]
            continue
        if kind == "reset":
            detector.reset()
        else:
            error = error_for(kind)

            async def make_request(bot: Any, method: Any, error: BaseException | None = error) -> str:
                if error is not None:
                    raise error
                return "response"

            try:
                outcomes.append(await detector(make_request, None, None))
            except (
                TelegramAPIError,
                aiohttp.ClientError,
                OSError,
                TimeoutError,
                aiohttp_socks.ProxyError,
                aiohttp_socks.ProxyConnectionError,
                aiohttp_socks.ProxyTimeoutError,
            ) as exc:
                outcomes.append(type(exc).__name__)
        for _ in range(4):
            await REAL_SLEEP(0)
        state_after.append([detector.failures, detector.troubled, detector.last_success])
    return {"outcomes": outcomes, "counts": counts, "state": state_after}


def drive_route(case: dict, kit: Any) -> list[dict]:
    session = kit.session()
    out: list[dict] = []
    for route in case["routes"]:
        kit.apply(session, route)
        proxy = session.proxy
        entry = {"current": kit.current(session), "proxy": None if not proxy else str(proxy)}
        if route == kit.direct:
            init = session._connector_init
            entry["connector"] = [session._connector_type.__name__, init["limit"], init["ttl_dns_cache"]]
        out.append(entry)
    return out
