from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup
from fakebot import FakeBot, OutboxLog, make_error, norm, record_sleeps

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "bot"


def load(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def inflate(value: Any) -> Any:
    if isinstance(value, dict) and len(value) == 1:
        ((key, body),) = value.items()
        if key == "parts":
            return "".join(char * count for char, count in body)
        if key == "bytes":
            return b"x" * body
        if key == "markup":
            return InlineKeyboardMarkup.model_validate(body)
    if isinstance(value, list):
        return [inflate(item) for item in value]
    return value


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
