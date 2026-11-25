from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from aiogram.client.default import Default
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from drivers_w2a import W2Bot, message_dict, user_dict
from drivers_w2b import (
    W2Link,
    _summary_stub,
    dump,
    local_tz,
    make_actions,
    make_sub,
    node_command_port,
    patched,
    svc_command_port,
    view,
)
from pydantic import BaseModel

CHAT = 12345
USER = {"id": 777, "first_name": "Тест"}


def full(value: Any) -> Any:
    if isinstance(value, BaseModel):
        fields = type(value).model_fields
        return {
            name: full(item)
            for name in fields
            if (item := getattr(value, name)) is not None and not isinstance(item, Default)
        }
    if isinstance(value, (bytes, bytearray)):
        return {"bytes": len(value)}
    if isinstance(value, dict):
        return {key: full(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [full(item) for item in value]
    return value


class W3Bot(W2Bot):
    def dump(self) -> list[list[Any]]:
        return [
            [name, full({k: v for k, v in kwargs.items() if v is not None or k == "parse_mode"})]
            for name, kwargs in self.calls
        ]

    async def me(self) -> Any:
        return SimpleNamespace(id=1, username="fakebot", is_bot=True)

    async def _invoke(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        result = await super()._invoke(name, args, kwargs)
        if name not in ("send_message", "edit_message_text") or not hasattr(result, "message_id"):
            return result
        chat = kwargs.get("chat_id")
        if chat is None and args:
            chat = args[0]
        body = {
            "message_id": result.message_id,
            "date": 1700000000,
            "chat": {"id": chat if chat is not None else CHAT, "type": "private"},
        }
        return Message.model_validate(body, context={"bot": self})


class State:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.writes: list[list[str]] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value
        self.writes.append([key, value])

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    async def keys(self, prefix: str) -> list[str]:
        return sorted(key for key in self.values if key.startswith(prefix))

    async def compare_and_set(self, key: str, old: str | None, new: str | None) -> bool:
        if self.values.get(key) != old:
            return False
        if new is None:
            self.values.pop(key, None)
        else:
            self.values[key] = new
        return True


class ProtoStore:
    def __init__(self, state: State) -> None:
        self.state = state
        self.events: list[list[Any]] = []

    async def get_state(self, key: str) -> str | None:
        return await self.state.get(key)

    async def set_state(self, key: str, value: str) -> None:
        await self.state.set(key, value)

    async def record_event(self, event_type: str, node: str | None, text: str, at: Any) -> None:
        self.events.append([event_type, node, text])


def build_message(spec: dict[str, Any], bot: Any) -> Message:
    body = {"update_id": 1, "message": message_dict({"chat": {"id": CHAT}, "user": USER, **spec})}
    return Update.model_validate(body, context={"bot": bot}).message


def build_callback(spec: dict[str, Any], bot: Any) -> Any:
    query: dict[str, Any] = {
        "id": spec.get("cb_id", "cb1"),
        "from": user_dict(USER),
        "chat_instance": "ci",
        "data": spec.get("data"),
    }
    if not spec.get("no_message"):
        query["message"] = message_dict({"chat": {"id": CHAT}, "user": USER})
    return Update.model_validate(
        {"update_id": 1, "callback_query": query}, context={"bot": bot}
    ).callback_query


async def feed(routers: list[Any], kind: str, event: Any, data: dict[str, Any]) -> bool:
    for router in routers:
        if await router.propagate_event(kind, event, **data) is not UNHANDLED:
            return True
    return False


def scrub(value: Any, table: dict[str, str]) -> Any:
    raw = json.dumps(value, ensure_ascii=False)
    for needle, token in table.items():
        raw = raw.replace(json.dumps(needle, ensure_ascii=False)[1:-1], token)
    return json.loads(raw)


def sub_of(kit: Any, case: dict[str, Any]) -> Any:
    return make_sub(kit.Subscription, case.get("sub"))


async def drive_basic(case: dict[str, Any], kit: Any) -> dict[str, Any]:
    bot = W3Bot()
    sub = sub_of(kit, case)
    apps = W2Link(case.get("apps"), kit.ns)
    table = {kit.about: "{ABOUT}"}
    with kit.setup(case, sub):
        if case["kind"] == "help":
            skills = make_actions(kit.ns, case["actions"])
            text = kit.build_help(sub, skills, case)
            return {"text": scrub(text, table), "about_used": kit.about in text}
        message = build_message({"text": case["text"]}, bot)
        data = kit.data(case, bot=bot, apps_link=apps, subscription=sub)
        handled = await feed(kit.routers, "message", message, data)
    return {
        "handled": handled,
        "calls": scrub(bot.dump(), table),
        "apps_calls": [c for c in apps.calls if c[0] == "command"],
    }


def wake_patches(case: dict[str, Any], wol: Any, log: list[Any]) -> list[tuple[Any, str, Any]]:
    pings = list(case.get("pings", []))

    async def ping_host(ip: str) -> bool:
        log.append(["ping", ip])
        return pings.pop(0) if pings else False

    def send_magic_packet(
        mac: str, broadcast: str = "255.255.255.255", port: int = 9, *rest: Any, **kw: Any
    ) -> None:
        log.append(["send", mac, broadcast, port])
        if case.get("send_error"):
            raise OSError(case["send_error"])

    async def wait_host_up(ip: str, timeout_s: float, *rest: Any, **kw: Any) -> float | None:
        log.append(["wait", ip, timeout_s])
        return case.get("wait")

    return [
        (wol, "ping_host", ping_host),
        (wol, "send_magic_packet", send_magic_packet),
        (wol, "wait_host_up", wait_host_up),
    ]


def view_items(kit: Any, status_kb: Any) -> list[tuple[Any, str, Any]]:
    items: list[tuple[Any, str, Any]] = []
    if kit.statusview is not None:
        items.append((kit.statusview, "build_summary_text", _summary_stub))
        items.append(
            (kit.statusview, "monitor_dst", lambda node_id: kit.ns.Address(node=node_id, service="monitor"))
        )
        items.append(
            (
                kit.statusview,
                "build_status_keyboard",
                lambda *a, **k: None if status_kb is None else InlineKeyboardMarkup.model_validate(status_kb),
            )
        )
    if kit.nodelinks is not None:
        items.append((kit.nodelinks, "node_command", node_command_port))
        items.append((kit.nodelinks, "svc_command", svc_command_port))
    return items


async def drive_node(case: dict[str, Any], kit: Any) -> dict[str, Any]:
    bot = W3Bot()
    state = State(case.get("state"))
    link = W2Link(case.get("link"), kit.ns)
    apps = W2Link(case.get("apps"), kit.ns)
    sub = sub_of(kit, case)
    spec = {
        "data": case["data"],
        "cb_id": case.get("cb_id", "cb1"),
        "no_message": case.get("no_message", False),
    }
    callback = build_callback(spec, bot)
    data = kit.data(case, bot=bot, app_state=state, node_link=link, apps_link=apps, subscription=sub)
    with local_tz(), patched(view_items(kit, case.get("status_kb"))), kit.setup():
        handled = await feed([kit.router], "callback_query", callback, data)
    return {
        "handled": handled,
        "calls": bot.dump(),
        "link_calls": [c for c in link.calls if c[0] == "command"],
        "apps_calls": [c for c in apps.calls if c[0] == "command"],
        "state": state.values,
    }


async def drive_wake(case: dict[str, Any], kit: Any) -> dict[str, Any]:
    bot = W3Bot()
    state = State(case.get("state"))
    link = W2Link(case.get("link"), kit.ns)
    log: list[Any] = []
    wake = SimpleNamespace(
        **{
            "mac": "",
            "ip": "",
            "broadcast": "255.255.255.255",
            "port": 9,
            "wait_timeout_s": 120.0,
            **case.get("wake", {}),
        }
    )
    guard = None
    if case.get("guard"):
        denial = case["guard"]

        async def guard(node_id: str) -> str | None:
            return denial.replace("{node}", node_id)

    with patched(wake_patches(case, kit.wol, log)):
        if case["kind"] == "command":
            event_kind, event = "message", build_message({"text": "/wake"}, bot)
        else:
            event_kind, event = (
                "callback_query",
                build_callback({"data": case["data"], "no_message": case.get("no_message", False)}, bot),
            )
        data = kit.data(case, bot=bot, app_state=state, node_link=link, wake_config=wake, wake_guard=guard)
        handled = await feed([kit.router], event_kind, event, data)
    return {
        "handled": handled,
        "calls": bot.dump(),
        "wol": log,
        "link_calls": link.calls,
        "state": state.values,
    }


async def drive_wakestate(case: dict[str, Any], kit: Any) -> dict[str, Any]:
    state = State(case.get("state"))
    out: list[Any] = []
    for op in case["ops"]:
        if op[0] == "put":
            await kit.put(state, op[1], op[2])
            out.append(None)
        else:
            out.append(await kit.get(state, op[1]))
    return {"out": out, "state": state.values}


class Sink:
    def __init__(self, fail: list[int] | None = None) -> None:
        self.fail = set(fail or ())
        self.sent: list[list[Any]] = []

    async def send_direct(self, chat_id: int, text: str, **kwargs: Any) -> int | None:
        extra = {
            key: (dump(value) if hasattr(value, "model_dump") else value)
            for key, value in kwargs.items()
            if value is not None
        }
        self.sent.append([chat_id, text, extra])
        return None if chat_id in self.fail else len(self.sent)


PERSONA = re.compile(r"\b\w{6}(?= заснул по тайм-ауту)")


def anon(value: Any) -> Any:
    return json.loads(PERSONA.sub("{PERSONA}", json.dumps(value, ensure_ascii=False)))


def render_value(value: Any) -> Any:
    return dump(value) if hasattr(value, "model_dump") else value


def drive_render(case: dict[str, Any], mod: Any) -> Any:
    return anon(render_value(getattr(mod, case["fn"])(*case["args"])))


async def drive_events(case: dict[str, Any], kit: Any, workdir: Any) -> dict[str, Any]:
    sink = Sink(case.get("fail"))
    book = kit.Book([make_sub(kit.Subscription, spec) for spec in case["subs"]])
    ctx, read = await kit.open(workdir, book, sink)
    routed: list[str] = []

    async def handle(_ctx: Any, env: Any) -> None:
        routed.append(env.payload["event"])

    routes = [kit.route(frozenset(names), handle) for names in case.get("routes", [])]
    src = case.get("src")
    envelope = kit.ns.Envelope(
        type="event",
        id="e1",
        src=None if src is None else kit.ns.Address(node=src.get("node"), service=src.get("service", "node")),
        payload=case["payload"],
    )
    handler = kit.build(ctx, routes)
    await handler(envelope)
    return anon({"sent": sink.sent, "events": await read(), "routed": routed})


REAL_SLEEP = asyncio.sleep


async def settle(rounds: int = 6) -> None:
    for _ in range(rounds):
        await REAL_SLEEP(0)


async def poll(check: Any, timeout: float = 5.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if check():
            return True
        await REAL_SLEEP(0.01)
    return check()


class Clock:
    def __init__(self, start: float) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def queue_of(items: list[Any]) -> Any:
    pending = list(items)

    def pop(default: Any) -> Any:
        return pending.pop(0) if pending else default

    return pop


def snapshot(mgr: Any, session: Any, kit: Any) -> list[Any]:
    return [
        mgr.route,
        mgr.label,
        mgr.ms,
        mgr.since,
        mgr.direct_ok,
        mgr.direct_streak,
        {key: mgr.bans[key] for key in sorted(mgr.bans)},
        kit.current(session),
    ]


async def drive_manager(case: dict[str, Any], kit: Any, workdir: Any) -> dict[str, Any]:
    session = kit.session()
    if case.get("initial_route"):
        kit.apply(session, case["initial_route"])
    clock = Clock(case.get("start", 1000.0))
    next_fetch = queue_of(case.get("fetches", []))
    next_probe = queue_of(case.get("probes", []))
    counts = {"fetch": 0, "probe": 0, "recovered": 0}
    switched: list[Any] = []
    path = workdir / "state" / "egress.json"
    if "file" in case:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            case["file"] if isinstance(case["file"], str) else json.dumps(case["file"]), encoding="utf-8"
        )

    async def fetch() -> list[dict[str, Any]]:
        counts["fetch"] += 1
        item = next_fetch([])
        if item == "raise":
            raise RuntimeError("fetch")
        return item

    async def probe() -> bool:
        counts["probe"] += 1
        return next_probe(True)

    async def on_switched(old: str, new: str, reason: str, details: list[dict[str, Any]]) -> None:
        switched.append([old, new, reason, details])

    def on_recovered() -> None:
        counts["recovered"] += 1

    mgr = kit.make(
        session, kit.cfg(**case.get("cfg", {})), fetch, probe, on_switched, path, clock, on_recovered
    )
    trail: list[Any] = [snapshot(mgr, session, kit)]
    for step in case.get("steps", []):
        kind = step[0]
        if kind == "advance":
            clock.now += step[1]
        elif kind == "trouble":
            await mgr.handle_trouble()
        elif kind == "tick":
            await mgr.tick()
        elif kind == "fail":
            mgr.detector.record_failure()
            await settle()
        elif kind == "success":
            mgr.detector.record_success()
            await settle()
        elif kind == "loop":
            mgr.start()
            for _ in range(3):
                mgr.detector.record_failure()
            await poll(lambda: counts["fetch"] >= 1)
            await mgr.stop()
        trail.append(snapshot(mgr, session, kit))
    files = sorted(entry.name for entry in path.parent.iterdir()) if path.parent.exists() else []
    return {
        "trail": trail,
        "switched": switched,
        "counts": counts,
        "state": mgr.state(),
        "saved": mgr.saved,
        "file": path.read_text(encoding="utf-8") if path.exists() else None,
        "files": files,
    }


def drive_state_file(case: dict[str, Any], kit: Any, workdir: Any) -> Any:
    path = workdir / case.get("subdir", "") / "egress.json"
    if "file" in case:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(case["file"], encoding="utf-8")
    if case.get("block_dir"):
        (workdir / "blocker").write_text("x", encoding="utf-8")
        path = workdir / "blocker" / "egress.json"
    out: dict[str, Any] = {}
    if "save" in case:
        kit.save(path, case["save"])
        out["file"] = path.read_text(encoding="utf-8") if path.exists() else None
        out["files"] = sorted(entry.name for entry in path.parent.iterdir()) if path.parent.is_dir() else []
    out["loaded"] = kit.load(path)
    return out


def drive_startup(case: dict[str, Any], kit: Any, workdir: Any) -> Any:
    path = workdir / "egress.json"
    if "file" in case:
        path.write_text(case["file"], encoding="utf-8")
    return kit.startup(kit.cfg(**case.get("cfg", {})), path)


class PickBot:
    def __init__(self, session: Any, outcomes: dict[str, bool], kit: Any) -> None:
        self.session = session
        self.outcomes = outcomes
        self.kit = kit
        self.tried: list[Any] = []

    async def get_me(self, request_timeout: Any = None) -> Any:
        route = self.kit.current(self.session)
        self.tried.append([route, request_timeout])
        if not self.outcomes.get(route, False):
            raise OSError("down")
        return SimpleNamespace(id=1)


async def drive_pick(case: dict[str, Any], kit: Any) -> dict[str, Any]:
    session = kit.session()
    bot = PickBot(session, case["outcomes"], kit)
    result = await kit.pick(bot, case["routes"])
    return {"result": result, "tried": bot.tried, "final": kit.current(session)}


class Flusher:
    def __init__(self) -> None:
        self.kicks = 0

    def kick(self) -> None:
        self.kicks += 1


async def drive_notices(case: dict[str, Any], kit: Any) -> dict[str, Any]:
    sink = Sink(case.get("fail"))
    book = kit.Book([make_sub(kit.Subscription, spec) for spec in case["subs"]])
    flusher = Flusher()
    clock = Clock(case.get("start", 100.0))
    notices = kit.notices(book, sink, flusher, clock)
    marks: list[int] = []
    for step in case["steps"]:
        if step[0] == "advance":
            clock.now += step[1]
        else:
            await notices.on_switched(*step[1:])
            marks.append(len(sink.sent))
    return {"sent": sink.sent, "marks": marks, "kicks": flusher.kicks}


def reports_of(kit: Any, specs: list[dict[str, Any]]) -> list[Any]:
    return [kit.ns.NodeReport(**spec) for spec in specs]


def drive_panel_view(case: dict[str, Any], kit: Any) -> Any:
    fn = case["fn"]
    mod = kit.mod
    sub = sub_of(kit, case)
    with local_tz():
        if fn == "build_panel_view":
            buttons = [InlineKeyboardButton(**spec) for spec in case.get("wake_buttons", [])]
            return view(
                mod.build_panel_view(
                    reports_of(kit, case["reports"]),
                    make_actions(kit.ns, case["app_actions"]),
                    datetime.fromisoformat(case["now"]),
                    buttons,
                )
            )
        if fn == "build_skills_list_view":
            return view(
                mod.build_skills_list_view(make_actions(kit.ns, case["app_actions"]), sub, case["offset"])
            )
        if fn == "build_skill_card_view":
            markup = case.get("markup")
            return view(
                mod.build_skill_card_view(
                    case["text"], None if markup is None else InlineKeyboardMarkup.model_validate(markup)
                )
            )
        if fn == "build_nodes_list_view":
            return view(
                mod.build_nodes_list_view(
                    reports_of(kit, case["reports"]),
                    case["offset"],
                    case["wakeable_ids"],
                    case["own_node_id"],
                )
            )
        return view(mod.build_update_result_view(case["text"]))


async def drive_panel(case: dict[str, Any], kit: Any) -> dict[str, Any]:
    bot = W3Bot()
    state = State(case.get("state"))
    link = W2Link(case.get("link"), kit.ns)
    apps = W2Link(case.get("apps"), kit.ns)
    sub = sub_of(kit, case)
    wake = SimpleNamespace(
        **{
            "mac": "",
            "ip": "",
            "broadcast": "255.255.255.255",
            "port": 9,
            "wait_timeout_s": 120.0,
            **case.get("wake", {}),
        }
    )
    if case["kind"] == "command":
        event_kind, event = "message", build_message({"text": case["text"]}, bot)
    else:
        spec = {"data": case["data"], "no_message": case.get("no_message", False)}
        event_kind, event = "callback_query", build_callback(spec, bot)
    data = kit.data(
        case, bot=bot, app_state=state, node_link=link, apps_link=apps, subscription=sub, wake_config=wake
    )
    with local_tz(), patched(view_items(kit, case.get("status_kb"))), kit.setup():
        handled = await feed([kit.router], event_kind, event, data)
    return {
        "handled": handled,
        "calls": bot.dump(),
        "link_calls": [c for c in link.calls if c[0] == "command"],
        "apps_calls": [c for c in apps.calls if c[0] == "command"],
        "state": state.values,
    }


async def drive_summary(case: dict[str, Any], kit: Any) -> Any:
    link = W2Link(case["link"], kit.ns)
    text = await kit.summary(link)
    return {"text": text, "calls": [c for c in link.calls if c[0] == "command"]}
