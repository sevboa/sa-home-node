from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import tempfile
from dataclasses import fields, is_dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from aiogram.exceptions import TelegramAPIError
from botfix import FIX, inflate
from fakebot import FakeBot, OutboxLog, make_error, norm, record_sleeps


async def play(case: dict, factory: Any, monkeypatch: Any) -> dict:
    sleeps = record_sleeps(monkeypatch)
    bot = FakeBot()
    for method, tokens in (case.get("script") or {}).items():
        bot.script[method] = [None if t == "ok" else make_error(t, str(i + 1)) for i, t in enumerate(tokens)]
    outbox = OutboxLog() if case.get("with_outbox", True) else None
    target = factory(bot, outbox)
    args = {key: inflate(value) for key, value in case["call"]["args"].items()}
    result = await getattr(target, case["call"]["method"])(**args)
    return {
        "result": list(result) if isinstance(result, tuple) else result,
        "calls": bot.dump(),
        "sleeps": sleeps,
        "outbox": outbox.rows if outbox is not None else None,
    }


class Ticker:
    def __init__(self) -> None:
        self.delays: list[float] = []
        self._gate = asyncio.Semaphore(0)
        self._real = asyncio.sleep

    async def sleep(self, delay: float, *args: Any, **kwargs: Any) -> None:
        self.delays.append(float(delay))
        await self._gate.acquire()

    async def tick(self, count: int) -> None:
        for _ in range(count):
            self._gate.release()
            for _ in range(4):
                await self._real(0)


async def drive_typing(case: dict, indicator: Any, context: Any) -> dict:
    ticker = Ticker()
    bot = FakeBot()
    for method, tokens in (case.get("script") or {}).items():
        bot.script[method] = [None if t == "ok" else make_error(t, str(i + 1)) for i, t in enumerate(tokens)]
    thread = case.get("message_thread_id")
    target = indicator(bot, 1001, thread)
    manager = context(bot, 1001, thread)
    original = asyncio.sleep
    asyncio.sleep = ticker.sleep
    try:
        for step in case["steps"]:
            name = step[0]
            if name == "tick":
                await ticker.tick(step[1])
            elif name == "enter":
                await manager.__aenter__()
            elif name == "exit":
                await manager.__aexit__(None, None, None)
            else:
                await getattr(target, name)()
        await target.stop()
    finally:
        asyncio.sleep = original
    return {"calls": bot.dump(), "sleep_calls": len(ticker.delays), "delays": sorted(set(ticker.delays))}


class StubNotifier:
    def __init__(self, fail: list[int]) -> None:
        self.fail = set(fail)
        self.sent: list[list[Any]] = []

    async def send_direct(self, chat_id: int, text: str, **kwargs: Any) -> int | None:
        self.sent.append([chat_id, text, norm({k: v for k, v in kwargs.items() if v is not None})])
        return None if chat_id in self.fail else len(self.sent)


async def drive_broadcast(case: dict, funcs: dict[str, Any], book: Any) -> dict:
    notifier = StubNotifier(case.get("fail", []))
    call = case["call"]
    name = call["fn"]
    kwargs = {key: inflate(value) for key, value in call.get("kwargs", {}).items()}
    if name == "admin_subs":
        return {"result": [sub.name for sub in funcs[name](book)], "sent": []}
    if name == "broadcast":
        subs = [sub for sub in book.all() if sub.chat_id in call["to"]]
        result = await funcs[name](subs, notifier, call["text"], **kwargs)
    else:
        result = await funcs[name](book, notifier, call["text"], **kwargs)
    return {"result": None if name == "notify_admins" else result, "sent": notifier.sent}


async def drive_linkwatch(case: dict, factory: Any) -> dict:
    now = [0.0]
    reconnects: list[float] = []

    async def on_reconnect(downtime: float) -> None:
        reconnects.append(downtime)
        if case.get("callback_raises"):
            raise RuntimeError("callback")

    middleware = factory(on_reconnect, case.get("threshold"), lambda: now[0])
    outcomes: list[str] = []
    for step in case["steps"]:
        if step[0] == "advance":
            now[0] += step[1]
            continue

        async def make_request(bot: Any, method: Any, kind: str = step[0]) -> str:
            if kind == "net":
                raise make_error("net")
            if kind == "bad":
                raise make_error("bad")
            return "response"

        try:
            outcomes.append(await middleware(make_request, None, None))
        except TelegramAPIError as exc:
            outcomes.append(type(exc).__name__)
        for _ in range(4):
            await asyncio.sleep(0)
    return {"outcomes": outcomes, "reconnects": reconnects}


def to_plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        plain = {field.name: to_plain(getattr(value, field.name)) for field in fields(value)}
        return {"class": type(value).__name__, **plain}
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: to_plain(item) for key, item in value.items()}
    return value


def attempt(function: Any, *args: Any) -> Any:
    try:
        return to_plain(function(*args))
    except (KeyError, TypeError, ValueError) as exc:
        return {"error": type(exc).__name__}


def parse_all(module: Any, extras: dict) -> dict:
    service = FIX.parent / "monitor" / "service"
    states = json.loads((service / "get-state.json").read_text(encoding="utf-8"))
    payloads = json.loads((service / "payloads.json").read_text(encoding="utf-8"))
    typed = {
        "event_payload": (module.parse_overheat_event, ["overheat_started", "overheat_cleared"]),
        "host_event_payload": (module.parse_host_event, ["host_degraded", "host_recovered"]),
        "smart_change_payload": (module.parse_smart_change, ["smart_degraded", "smart_recovered"]),
    }
    plain = {
        "health_dict": module.parse_health_state,
        "host_state_dict": module.parse_host_metric_state,
        "outage_dict": module.parse_outage,
    }
    result: dict[str, Any] = {"get_state": {}, "payloads": {}, "extras": {}}
    for name, state in states.items():
        result["get_state"][name] = {
            "health": [attempt(module.parse_health_state, raw) for raw in state["health"]],
            "host_health": [attempt(module.parse_host_metric_state, raw) for raw in state["host_health"]],
            "disks": [attempt(module.parse_disk_summary, raw) for raw in state["disks"]],
            "last_outage": attempt(module.parse_outage, state["last_outage"]),
        }
    for name, (function, types) in typed.items():
        result["payloads"][name] = [
            attempt(function, types[index % len(types)], raw) for index, raw in enumerate(payloads[name])
        ]
    for name, function in plain.items():
        result["payloads"][name] = [attempt(function, raw) for raw in payloads[name]]
    functions = {
        "health": module.parse_health_state,
        "host_state": module.parse_host_metric_state,
        "disk": module.parse_disk_summary,
        "outage": module.parse_outage,
    }
    for name, function in functions.items():
        result["extras"][name] = [attempt(function, raw) for raw in extras[name]]
    for name, function in (
        ("overheat", module.parse_overheat_event),
        ("host_event", module.parse_host_event),
        ("smart", module.parse_smart_change),
    ):
        result["extras"][name] = [attempt(function, "t", raw) for raw in extras[name]]
    return result


TOKEN = "link-token"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LinkHandler:
    def __init__(self, kit: Any) -> None:
        self.kit = kit
        self.actions = ["echo", "noop"]
        self.delay = 0.0

    def describe(self) -> Any:
        return self.kit.description(self.actions)

    async def get_state(self) -> dict[str, Any]:
        return {"service": "stub", "n": 1}

    async def run_command(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        if self.delay:
            await asyncio.sleep(self.delay)
        return {"action": action, "args": args}


async def until(check: Any, timeout: float = 5.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if check():
            return True
        await asyncio.sleep(0.02)
    return False


def failure(exc: BaseException, kit: Any) -> dict[str, Any]:
    return {
        "error": type(exc).__name__,
        "unavailable": isinstance(exc, kit.Unavailable),
        "timeout": isinstance(exc, kit.Timeout),
        "code": getattr(exc, "code", None),
        "head": str(exc).split(":")[0],
    }


async def drive_servicelink(case: dict, kit: Any) -> dict:
    handler = LinkHandler(kit)
    tmp = tempfile.mkdtemp(prefix="bs-link-")
    tcp = case.get("tcp", False)
    port = free_port() if tcp else 0
    endpoint = f"tcp://127.0.0.1:{port}" if tcp else f"unix:{os.path.join(tmp, 'svc.sock')}"
    state = SimpleNamespace(server=None, link=None, proxy=None, events=[], connects=[])

    async def on_event(env: Any) -> None:
        state.events.append(env.payload)

    async def on_connected() -> None:
        state.connects.append(1)
        if case.get("hook_raises"):
            raise RuntimeError("hook")

    trace: list[Any] = []
    try:
        for step in case["steps"]:
            name = step[0]
            result: Any = None
            if name == "start_server":
                state.server = kit.Server(endpoint, handler, token=TOKEN if tcp else "")
                await state.server.start()
                if tcp and state.proxy is None:
                    state.proxy = kit.Proxy("127.0.0.1", port)
                    await state.proxy.start()
            elif name == "stop_server":
                await state.server.stop()
            elif name == "start_link":
                target = state.proxy.endpoint if state.proxy is not None else endpoint
                state.link = kit.Link(
                    target,
                    token=TOKEN if tcp else "",
                    display_name="svc",
                    on_event=on_event,
                    on_connected=on_connected,
                    reconnect_delay=0.05,
                )
                await state.link.start()
            elif name == "stop_link":
                await state.link.stop()
            elif name == "wait_connected":
                result = await until(lambda: state.link.connected)
            elif name == "wait_disconnected":
                result = await until(lambda: not state.link.connected)
            elif name == "connected":
                result = state.link.connected
            elif name == "handler":
                handler.actions = step[1].get("actions", handler.actions)
                handler.delay = step[1].get("delay", handler.delay)
            elif name == "freeze":
                state.proxy.freeze()
            elif name == "unfreeze":
                state.proxy.unfreeze()
            elif name == "broadcast":
                before = len(state.events)
                result = await state.server.broadcast_event(step[1], step[2])
                await until(lambda before=before: len(state.events) > before)
            elif name == "events":
                result = state.events
            elif name == "connects":
                await until(lambda need=step[1]: len(state.connects) >= need)
                result = len(state.connects)
            else:
                try:
                    if name == "get_state":
                        result = {"ok": await state.link.get_state()}
                    elif name == "command":
                        reply = await state.link.command(step[1], step[2], timeout=step[3])
                        result = {"ok": reply}
                    elif name == "describe":
                        description = await state.link.describe()
                        result = None if description is None else [a.id for a in description.actions]
                    else:
                        result = [a.id for a in await state.link.actions()]
                except (kit.Unavailable, kit.ProtoError, ConnectionError, OSError) as exc:
                    result = failure(exc, kit)
            trace.append(result)
    finally:
        if state.link is not None:
            await state.link.stop()
        if state.server is not None:
            await state.server.stop()
        if state.proxy is not None:
            await state.proxy.stop()
    return {"trace": trace}


class Warnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1


async def drive_menu(case: dict, data: dict, kit: Any) -> dict:
    bot = FakeBot()
    for method, tokens in (case.get("script") or {}).items():
        bot.script[method] = [None if t == "ok" else make_error(t, str(i + 1)) for i, t in enumerate(tokens)]
    subs = {name: kit.sub(name, spec) for name, spec in data["subs"].items()}
    actions = [kit.action(spec) for spec in data["actions"]] if case.get("actions", True) else []
    call = case["call"]
    watcher = Warnings()
    logging.getLogger().addHandler(watcher)
    try:
        commands = None
        if call["fn"] == "build":
            built = kit.build(subs.get(call["sub"]), actions)
            commands = [[item.command, item.description] for item in built]
        elif call["fn"] == "refresh":
            await kit.refresh(bot, call["chat"], subs.get(call["sub"]), actions)
        else:
            await kit.set_all(bot, kit.book([subs[name] for name in call["book"]]), actions)
    finally:
        logging.getLogger().removeHandler(watcher)
    return {"commands": commands, "calls": bot.dump(), "warnings": watcher.count}
