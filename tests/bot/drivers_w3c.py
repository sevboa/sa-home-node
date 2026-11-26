from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiogram import Dispatcher
from aiogram.client.default import Default
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from drivers import StubNotifier
from drivers_w2a import W2Bot, build_update, code_text, rows_of, sub_view
from drivers_w2b import W2Link, dump, local_tz, make_actions, make_sub, patched
from pydantic import BaseModel

WELCOME = "🔑 Приглашение принято — добро пожаловать."
EXPIRY = re.compile(r"— до \d{4}-\d{2}-\d{2} \d{2}:\d{2}")
PRODUCT = re.compile(r"Сообщите код \S+ или воспользуйтесь")
TIME_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
DISPATCHERS: dict[tuple[int, ...], Dispatcher] = {}


def plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return {
            name: plain(item)
            for name in type(value).model_fields
            if (item := getattr(value, name)) is not None and not isinstance(item, Default)
        }
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


class W3Bot(W2Bot):
    id = 7

    def dump(self) -> list[list[Any]]:
        return [
            [name, plain({k: v for k, v in kwargs.items() if v is not None or k == "parse_mode"})]
            for name, kwargs in self.calls
        ]

    async def usernames(self) -> list[str]:
        return ["fake_bot"]

    async def me(self) -> Any:
        return SimpleNamespace(id=7, username="fake_bot", usernames=None, is_bot=True)


def dispatcher_for(routers: list[Any]) -> Dispatcher:
    key = tuple(id(router) for router in routers)
    if key not in DISPATCHERS:
        dp = Dispatcher()
        for router in routers:
            dp.include_router(router)
        DISPATCHERS[key] = dp
    return DISPATCHERS[key]


async def feed(routers: list[Any], bot: Any, spec: dict, data: dict) -> bool:
    update = build_update(spec, bot)
    result = await dispatcher_for(routers).feed_update(bot, update, **data)
    return result is not UNHANDLED


def resolve_ago(value: Any, now: datetime) -> Any:
    if isinstance(value, dict):
        if set(value) == {"ago"}:
            return (now - timedelta(seconds=value["ago"])).isoformat()
        return {key: resolve_ago(item, now) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_ago(item, now) for item in value]
    return value


def fix_calls(calls: list, issued: list[str] | None = None) -> list:
    text = json.dumps(calls, ensure_ascii=False)
    for index, code in enumerate(issued or ()):
        text = text.replace(f"{code[:4]}-{code[4:]}", f"<printed#{index}>").replace(code, f"<code#{index}>")
    out = json.loads(text)
    for call in out:
        body = call[1]
        if isinstance(body.get("text"), str):
            if body["text"].startswith(WELCOME):
                body["text"] = WELCOME
            body["text"] = TIME_LINE.sub("<now>", body["text"])
            body["text"] = EXPIRY.sub("— до <время>", body["text"])
            body["text"] = PRODUCT.sub("Сообщите код <имя> или воспользуйтесь", body["text"])
    return out


def spec_text(spec: dict, issued: list[str]) -> dict:
    out = dict(spec)
    for key in ("text", "data"):
        if key in out and isinstance(out[key], dict):
            out[key] = code_text(out[key], issued)
    return out


def sub_factory(cls: Any) -> Any:
    def make(name: str, chat_id: int, rights: Any, **extra: Any) -> Any:
        for key in ("event_types", "except_event_types"):
            if key in extra:
                extra[key] = frozenset(extra[key])
        return cls(name=name, chat_id=chat_id, allowed_commands=frozenset(rights), **extra)

    return make


def sub_of(kit: Any, spec: list) -> Any:
    name, chat, rights, *rest = spec
    return kit.sub(name, chat, rights, **(rest[0] if rest else {}))


async def drive_invites(case: dict, kit: Any, workdir: Path) -> dict:
    store, close = await kit.open_invites(workdir / "gate.sqlite")
    book = kit.book([sub_of(kit, spec) for spec in case["subs"]])
    guest_path = workdir / "guests.toml" if case.get("guest_file", True) else None
    guest_store = kit.guest_store(guest_path)
    notifier = StubNotifier([])
    gate = kit.Gatekeeper(kit.cfg(case["cfg"]), store, book, guest_store, notifier)
    catalog = kit.catalog(case.get("catalog"))
    apps = W2Link({"actions": case.get("apps_actions", [])}, kit.ns)
    issued: list[str] = []
    admissions: list[Any] = []
    menu: list[Any] = []
    hooks: list[Any] = []

    def ref(code: str) -> str:
        return f"@{issued.index(code)}" if code in issued else code

    async def usage(*args: Any) -> str:
        return case.get("usage", "").replace("{chat}", str(args[-1]))

    async def hook(message: Any, admission: Any, *rest: Any, **named: Any) -> None:
        hooks.append([message.chat.id, ref(admission.code)])

    def record_menu(bot: Any, chat_id: int, subscription: Any, *args: Any, **named: Any) -> Any:
        extra = named.get("skill_actions") or named.get("app_actions")
        if extra is None:
            extra = next((arg for arg in args if isinstance(arg, (list, tuple))), ())
        menu.append(
            [
                chat_id,
                None if subscription is None else [subscription.name, sorted(subscription.allowed_commands)],
                [a.id for a in extra],
            ]
        )

        async def done() -> None:
            return None

        return done()

    trace: list[Any] = []
    try:
        with kit.patch_menu(record_menu, usage):
            for step in case["steps"]:
                name = step[0]
                if name == "issue":
                    code, _ = await gate.issue(step[1], step[2], step[3])
                    issued.append(code)
                    trace.append(None)
                elif name == "admit":
                    admission = await gate.try_admit(
                        step[1], code_text(step[2], issued), **(step[3] if len(step) > 3 else {})
                    )
                    admissions.append(admission)
                    trace.append(None if admission is None else ref(admission.code))
                elif name == "revoke":
                    trace.append(await gate.revoke_code(issued[step[1]]))
                elif name == "update":
                    opts = step[2] if len(step) > 2 else {}
                    bot = W3Bot()
                    start = (len(notifier.sent), len(menu), len(hooks))
                    admission = admissions[opts["admission"]] if "admission" in opts else None
                    ctx = SimpleNamespace(
                        gate=gate,
                        book=book,
                        guest_store=guest_store,
                        notifier=notifier,
                        catalog=catalog,
                        apps=apps,
                        usage=usage,
                        hook=hook if opts.get("hook") else None,
                        admission=admission,
                        username=opts.get("username", "fake_bot"),
                    )
                    handled = await feed(kit.routers, bot, spec_text(step[1], issued), kit.data(ctx))
                    for row in await gate.open_codes():
                        if row["code"] not in issued:
                            issued.append(row["code"])
                    trace.append(
                        {
                            "handled": handled,
                            "calls": fix_calls(bot.dump(), issued),
                            "sent": fix_calls(
                                [
                                    ["s", {"text": row[1], "chat": row[0]}]
                                    for row in notifier.sent[start[0] :]
                                ],
                                issued,
                            ),
                            "menu": menu[start[1] :],
                            "hook": hooks[start[2] :],
                        }
                    )
                elif name == "open":
                    rows = await gate.open_codes()
                    trace.append([[ref(r["code"]), r["issued_by_chat_id"]] for r in rows])
                elif name == "guests":
                    trace.append([sub_view(sub) for sub in book.guests()])
                elif name == "file":
                    if guest_path is None or not guest_path.exists():
                        trace.append(None)
                    else:
                        text = guest_path.read_text(encoding="utf-8")
                        trace.append(re.sub(r'invited_at = "[^"]*"', 'invited_at = "<now>"', text))
    finally:
        await close()
    return {"trace": trace}


async def drive_alerts(case: dict, kit: Any, workdir: Path) -> dict:
    events, close = await kit.open_events(workdir / "alerts.sqlite")
    kit.prepare(workdir / "alerts.sqlite", ["cpu:x", "cpu:y", "gpu:0", "disk:/dev/sda"])
    book = kit.book([sub_of(kit, spec) for spec in case["subs"]])
    notifier = StubNotifier(case.get("fail", []))
    dispatcher = kit.Dispatcher(notifier, book, events)
    handler = kit.build_event_handler(dispatcher)
    trace: list[Any] = []

    def result_of(result: Any) -> dict:
        return {"delivered": result.delivered, "handled": result.handled}

    try:
        with local_tz():
            for step in case["steps"]:
                name = step[0]
                start = len(notifier.sent)
                item: dict[str, Any] = {}
                if name == "alert":
                    item["result"] = result_of(
                        await dispatcher.dispatch_alert(kit.parse.parse_overheat_event(step[1], step[2]))
                    )
                elif name == "clear":
                    item["result"] = result_of(
                        await dispatcher.dispatch_clear(kit.parse.parse_overheat_event(step[1], step[2]))
                    )
                elif name == "host_alert":
                    item["result"] = result_of(
                        await dispatcher.dispatch_host_alert(kit.parse.parse_host_event(step[1], step[2]))
                    )
                elif name == "host_clear":
                    item["result"] = result_of(
                        await dispatcher.dispatch_host_clear(kit.parse.parse_host_event(step[1], step[2]))
                    )
                elif name == "smart":
                    item["result"] = result_of(
                        await dispatcher.dispatch_smart(kit.parse.parse_smart_change(step[1], step[2]))
                    )
                elif name == "envelope":
                    item["result"] = await handler(kit.envelope(step[1]))
                elif name == "fail":
                    notifier.fail = set(step[1])
                    trace.append(None)
                    continue
                item["sent"] = notifier.sent[start:]
                trace.append(item)
        rows = rows_of(
            workdir / "alerts.sqlite",
            "SELECT component_id, chat_id, kind, message_id FROM health_notifications ORDER BY 1, 2, 3",
        )
    finally:
        await close()
    return {"trace": trace, "notifications": rows}


def fix_text(text: str | None) -> str | None:
    return None if text is None else TIME_LINE.sub("<now>", text)


def node_of(dst: Any) -> Any:
    return getattr(dst, "node", None)


async def drive_statusview(case: dict, kit: Any) -> Any:
    fn = case["fn"]
    mod = kit.mod
    link = W2Link(case.get("link"), kit.ns)
    sub = make_sub(kit.ns.Subscription, case.get("sub"))
    with local_tz():
        if fn == "monitor_dst":
            dst = mod.monitor_dst(case["node_id"])
            return {"node": dst.node, "service": dst.service}
        if fn == "build_status_keyboard":
            actions = make_actions(kit.ns, case.get("monitor_actions", []))
            return {"markup": dump(mod.build_status_keyboard(sub, actions, case.get("node_id")))}
        if fn == "build_downtime_page":
            text, markup = await mod.build_downtime_page(link, case["offset"], case.get("node_id"))
            return {"text": text, "markup": dump(markup), "calls": link.calls}
        dst = None if case.get("dst") is False else mod.monitor_dst(case.get("node_id"))
        text = await getattr(mod, fn)(link, dst)
        return {"text": fix_text(text), "calls": link.calls}


def stub_markup(tag: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tag, callback_data=f"x:{tag}")]])


def view_stubs(log: list, link: Any) -> dict[str, Any]:
    def name_of(sub: Any) -> Any:
        return None if sub is None else sub.name

    async def node_card(
        node_link: Any, subscription: Any, node_id: Any = None, *rest: Any, **named: Any
    ) -> Any:
        log.append(["node_card", name_of(subscription), node_id, node_link is link])
        return f"карточка {node_id}", stub_markup(f"card:{node_id}")

    async def service_card(
        node_link: Any, subscription: Any, service_name: Any, node_id: Any = None, *rest: Any, **named: Any
    ) -> Any:
        log.append(["service_card", name_of(subscription), service_name, node_id, node_link is link])
        return f"служба {service_name}@{node_id}", stub_markup(f"svc:{service_name}")

    async def swarm(node_link: Any, subscription: Any, wake: Any = None, *rest: Any, **named: Any) -> Any:
        log.append(["swarm", name_of(subscription), getattr(wake, "mac", None), node_link is link])
        return "рой", stub_markup("swarm")

    async def full(node_link: Any, dst: Any = None, **named: Any) -> str:
        log.append(["full", node_of(dst), getattr(dst, "service", None), node_link is link])
        return f"полный {node_of(dst)}"

    async def stats(node_link: Any, dst: Any = None, **named: Any) -> str:
        log.append(["stats", node_of(dst), getattr(dst, "service", None), node_link is link])
        return f"статистика {node_of(dst)}"

    async def downtime(node_link: Any, offset: int = 0, node_id: Any = None, *rest: Any, **named: Any) -> Any:
        if node_id is None and "dst" in named:
            node_id = node_of(named["dst"])
        log.append(["downtime", offset, node_id, node_link is link])
        return f"отключения {offset} {node_id}", stub_markup(f"dt:{offset}")

    return {
        "node_card": node_card,
        "service_card": service_card,
        "swarm": swarm,
        "full": full,
        "stats": stats,
        "downtime": downtime,
    }


def seed_ticks(spec: Any, now: datetime) -> str:
    return json.dumps([(now - timedelta(seconds=ago)).isoformat() for ago in spec])


async def drive_status_handlers(case: dict, kit: Any, workdir: Path) -> dict:
    state, close = await kit.open_state(workdir / "status.sqlite")
    now = datetime.now(UTC)
    for key, value in case.get("seed", {}).items():
        await kit.state_set(
            state,
            key,
            seed_ticks(value, now) if isinstance(value, list) and key.startswith("action_ticks:") else value,
        )
    sub = make_sub(kit.ns.Subscription, case.get("sub"))
    link = W2Link(case.get("link"), kit.ns)
    wake = None if case.get("wake") is None else SimpleNamespace(mac=case["wake"])
    log: list[Any] = []
    stubs = view_stubs(log, link)
    trace: list[Any] = []
    try:
        with patched([(mod, attr, stubs[key]) for key, (mod, attr) in kit.targets.items()]):
            for step in case["steps"]:
                if step[0] == "update":
                    bot = W3Bot()
                    start = (len(log), len(link.calls))
                    ctx = SimpleNamespace(link=link, sub=sub, wake=wake, state=state)
                    handled = await feed(kit.routers, bot, step[1], kit.data(ctx))
                    trace.append(
                        {
                            "handled": handled,
                            "calls": fix_calls(bot.dump()),
                            "log": log[start[0] :],
                            "link": link.calls[start[1] :],
                        }
                    )
                else:
                    raw = await kit.state_get(state, step[1])
                    trace.append(None if raw is None else len(json.loads(raw)))
    finally:
        await close()
    return {"trace": trace}


async def drive_nodelinks(case: dict, kit: Any) -> dict:
    sub = make_sub(kit.ns.Subscription, case.get("sub"))
    link = W2Link(case.get("link"), kit.ns)
    log: list[Any] = []
    stubs = view_stubs(log, link)
    trace: list[Any] = []
    with patched([(mod, attr, stubs[key]) for key, (mod, attr) in kit.targets.items()]):
        for text in case["texts"]:
            bot = W3Bot()
            start = (len(log), len(link.calls))
            handled = await feed(
                kit.routers,
                bot,
                {
                    "kind": "message",
                    "chat": {"id": 1001, "type": "private"},
                    "user": {"id": 1, "first_name": "x"},
                    "text": text,
                },
                kit.data(SimpleNamespace(link=link, sub=sub)),
            )
            seen: list[Any] = []
            for call in link.calls[start[1] :]:
                if call not in seen:
                    seen.append(call)
            trace.append(
                {"handled": handled, "calls": fix_calls(bot.dump()), "log": log[start[0] :], "link": seen}
            )
    return {"trace": trace}


def make_reports(kit: Any, specs: list[dict]) -> list[Any]:
    return [kit.NodeReport(**spec) for spec in specs]


async def drive_swarm(case: dict, kit: Any, workdir: Path) -> Any:
    with patched(getattr(kit, "patches", [])):
        return await _drive_swarm(case, kit, workdir)


async def _drive_swarm(case: dict, kit: Any, workdir: Path) -> Any:
    state, close = await kit.open_state(workdir / "swarm.sqlite")
    now = datetime.now(UTC)
    try:
        for key, value in case.get("seed", {}).items():
            await kit.state_set(state, key, value)
        fn = case["fn"]
        sub = make_sub(kit.ns.Subscription, case.get("sub"))
        wake = None if case.get("wake") is None else SimpleNamespace(mac=case["wake"])
        reports = make_reports(kit, resolve_ago(case.get("reports", []), now))
        when = datetime.fromisoformat(case["now"]) if "now" in case else now
        if fn == "order_by_health":
            return [r.node_id for r in kit.mod.order_by_health(reports)]
        if fn == "node_line":
            return kit.mod.node_line(reports[0])
        if fn == "last_failure_line":
            return kit.mod.last_failure_line(reports, when)
        if fn == "render_swarm":
            return kit.mod.render_swarm(reports, wake, when)
        if fn == "wake_rows":
            return [[b.text, b.callback_data] for b in kit.mod.wake_rows(sub, wake)]
        if fn == "offline_wake_rows":
            return [[b.text, b.callback_data] for b in await kit.mod.offline_wake_rows(sub, state, reports)]
        if fn == "build_swarm_keyboard":
            result = await kit.mod.build_swarm_keyboard(
                None if case.get("sub") is None else sub,
                wake,
                reports,
                state if case.get("with_state", True) else None,
            )
            return {"markup": dump(result)}
        link = W2Link(resolve_ago(case["link"], now), kit.ns)
        text, markup = await kit.mod.build_swarm_view(
            link,
            None if case.get("sub") is None else sub,
            wake,
            state if case.get("with_state", True) else None,
        )
        keys = {key: await kit.state_get(state, key) for key in case.get("check", [])}
        return {
            "text": fix_text(text),
            "markup": dump(markup),
            "state": keys,
            "calls": sorted(link.calls, key=str),
        }
    finally:
        await close()
