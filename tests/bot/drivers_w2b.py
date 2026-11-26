from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import os
import re
import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aiogram.types import InlineKeyboardMarkup

FIX = Path(__file__).resolve().parents[1] / "fixtures"
STATES = json.loads((FIX / "monitor" / "service" / "get-state.json").read_text(encoding="utf-8"))
ZONE = "Etc/GMT-5"
SUMMARY_STUB = "📟 сводка монитора"


@contextlib.contextmanager
def local_tz() -> Iterator[None]:
    saved = os.environ.get("TZ")
    os.environ["TZ"] = ZONE
    time.tzset()
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        time.tzset()


def import_optional(name: str) -> Any:
    try:
        return importlib.import_module(f"sa_home_node.bot.{name}")
    except ImportError:
        return None


def dump(markup: Any) -> Any:
    return None if markup is None else markup.model_dump(exclude_none=True)


def view(result: Any) -> dict[str, Any]:
    text, markup = result
    return {"text": text, "markup": dump(markup)}


def make_sub(cls: Any, spec: dict[str, Any] | None) -> Any:
    if spec is None:
        return None
    return cls(
        name=spec["name"],
        chat_id=spec["chat_id"],
        allowed_commands=frozenset(spec.get("allowed_commands", ())),
        event_types=frozenset(spec.get("event_types", ())),
        except_event_types=frozenset(spec.get("except_event_types", ())),
        broken=spec.get("broken", False),
        source=spec.get("source", "config"),
        invited_at=spec.get("invited_at", ""),
        invited_user=spec.get("invited_user", ""),
    )


def make_exc(ns: Any, token: str) -> Exception:
    kind, _, rest = token.partition(":")
    if kind == "unavailable":
        return ns.ServiceUnavailableError("служба недоступна")
    if kind == "proto":
        code, _, message = rest.partition(":")
        return ns.ProtoError(code, message)
    if kind == "timeout":
        return TimeoutError("таймаут")
    if kind == "oserror":
        return OSError("сбой")
    raise ValueError(token)


class W2Link:
    def __init__(self, spec: dict[str, Any] | None, ns: Any) -> None:
        spec = spec or {}
        self.spec = spec
        self.ns = ns
        self.connected = spec.get("connected", True)
        self.display_name = "демо"
        self.calls: list[list[Any]] = []

    @staticmethod
    def key(dst: Any) -> str:
        node = getattr(dst, "node", None)
        service = getattr(dst, "service", None)
        return f"{node or '-'}/{service or '-'}"

    def _pick(self, table: str, key: str) -> Any:
        reply = self.spec.get(table, {}).get(key)
        if isinstance(reply, str):
            raise make_exc(self.ns, reply)
        return reply

    async def get_state(self, dst: Any = None) -> dict[str, Any]:
        key = self.key(dst)
        self.calls.append(["get_state", key])
        reply = self._pick("get_state", key)
        if reply is None:
            raise make_exc(self.ns, "unavailable")
        return reply

    async def command(
        self,
        action: str,
        args: dict[str, Any] | None = None,
        dst: Any = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        key = self.key(dst)
        self.calls.append(["command", action, args, key])
        reply = self._pick("commands", f"{key}:{action}")
        if reply is None:
            raise make_exc(self.ns, "unavailable")
        return reply

    async def describe(self, dst: Any = None) -> Any:
        if not self.connected:
            return None
        key = self.key(dst)
        self.calls.append(["describe", key])
        reply = self.spec.get("describe", {}).get(key)
        if reply is None or isinstance(reply, str):
            return None
        payload = {"node": "n1", "service": "svc", "version": "1", **reply}
        return self.ns.ServiceDescription.from_payload(payload)

    async def actions(self) -> tuple[Any, ...]:
        self.calls.append(["actions"])
        if not self.connected:
            return ()
        return tuple(self.ns.ActionSpec.from_dict(a) for a in self.spec.get("actions", ()))


def make_actions(ns: Any, specs: list[dict[str, Any]]) -> list[Any]:
    return [ns.ActionSpec.from_dict(spec) for spec in specs]


def drive_alerts(case: dict[str, Any], mod: Any, parse: Any) -> str:
    fn = case["fn"]
    if fn == "render_event":
        arg = parse.parse_overheat_event(case["type"], case["data"])
    elif fn == "render_host_event":
        arg = parse.parse_host_event(case["type"], case["data"])
    elif fn == "render_smart_change":
        arg = parse.parse_smart_change(case["type"], case["data"])
    else:
        arg = parse.parse_outage(case["data"])
    with local_tz():
        return getattr(mod, fn)(arg)


def wire_of(case: dict[str, Any]) -> dict[str, Any]:
    return STATES[case["state"]] if "state" in case else case["wire"]


def drive_status(case: dict[str, Any], mod: Any, parse: Any) -> str:
    fn = case["fn"]
    call = getattr(mod, fn)
    with local_tz():
        if fn == "render_status_summary":
            wire = wire_of(case)
            states = [parse.parse_health_state(h) for h in wire.get("health", [])]
            th = wire.get("thresholds", {})
            uptime = wire.get("uptime_s")
            return call(
                datetime.fromisoformat(case["now"]),
                timedelta(seconds=uptime) if uptime is not None else None,
                [s for s in states if s.kind == "cpu"],
                [s for s in states if s.kind == "gpu"],
                [parse.parse_disk_summary(d) for d in wire.get("disks", [])],
                parse.parse_outage(wire.get("last_outage")),
                cpu_warn_c=th.get("cpu", {}).get("warn_c", 0.0),
                cpu_crit_c=th.get("cpu", {}).get("crit_c", 0.0),
                gpu_warn_c=th.get("gpu", {}).get("warn_c", 0.0),
                gpu_crit_c=th.get("gpu", {}).get("crit_c", 0.0),
                disk_warn_c=th.get("disk", {}).get("warn_c", 0.0),
                disk_crit_c=th.get("disk", {}).get("crit_c", 0.0),
            )
        if fn == "render_status_full":
            return call([parse.parse_health_state(h) for h in wire_of(case).get("health", [])])
        if fn == "render_stats":
            wire = wire_of(case)
            return call(wire.get("job_counts", {}), wire.get("recent_runs", []))
        if fn == "render_downtime":
            events = [e for e in (parse.parse_outage(raw) for raw in case["events"]) if e is not None]
            return call(events, case["offset"])
        if fn == "render_host_block":
            return call([parse.parse_host_metric_state(h) for h in case["items"]])
        if fn == "render_state_line":
            return call(parse.parse_health_state(case["item"]))
        if fn == "render_host_line":
            return call(parse.parse_host_metric_state(case["item"]))
        if fn == "render_disk_line":
            return call(parse.parse_disk_summary(case["item"]), case["warn"], case["crit"])
        return call(parse.parse_health_state(case["item"]), case["warn"], case["crit"])


def _norm_name(name: str) -> str | None:
    norm = name.lower().replace("-", "_")
    return norm if re.fullmatch(r"[a-z0-9_]+", norm) else None


def _link_command(prefix: str, *parts: str) -> str | None:
    norms = [_norm_name(p) for p in parts]
    if any(n is None for n in norms):
        return None
    name = prefix + "_".join(norms)
    return f"/{name}" if len(name) <= 32 else None


def node_command_port(node_id: str) -> str | None:
    return _link_command("node_", node_id)


def svc_command_port(node_id: str, service: str) -> str | None:
    return _link_command("svc_", node_id, service)


@contextlib.contextmanager
def patched(items: list[tuple[Any, str, Any]]) -> Iterator[None]:
    missing = object()
    saved = [(obj, name, getattr(obj, name, missing)) for obj, name, _ in items]
    for obj, name, value in items:
        setattr(obj, name, value)
    try:
        yield
    finally:
        for obj, name, old in saved:
            if old is missing:
                delattr(obj, name)
            else:
                setattr(obj, name, old)


async def _summary_stub(*args: Any, **kwargs: Any) -> str:
    return SUMMARY_STUB


def drive_nodeview(case: dict[str, Any], mod: Any, ns: Any) -> Any:
    return asyncio.run(_drive_nodeview(case, mod, ns))


async def _drive_nodeview(case: dict[str, Any], mod: Any, ns: Any) -> Any:
    sub = make_sub(ns.Subscription, case.get("sub"))
    link = W2Link(case.get("link"), ns)
    status_kb = case.get("status_kb")
    items: list[tuple[Any, str, Any]] = []
    if ns.statusview is not None:
        items.append((ns.statusview, "build_summary_text", _summary_stub))
        items.append(
            (ns.statusview, "monitor_dst", lambda node_id: ns.Address(node=node_id, service="monitor"))
        )
        if ns.stub_keyboard:
            items.append(
                (
                    ns.statusview,
                    "build_status_keyboard",
                    lambda *a, **k: (
                        None if status_kb is None else InlineKeyboardMarkup.model_validate(status_kb)
                    ),
                )
            )
    if ns.nodelinks is not None:
        items.append((ns.nodelinks, "node_command", node_command_port))
        items.append((ns.nodelinks, "svc_command", svc_command_port))
    fn = case["fn"]
    with local_tz(), patched(items):
        if fn in ("node_segment", "node_from_segment", "node_unavailable_text"):
            return getattr(mod, fn)(*case["args"])
        if fn in (
            "restart_deferred_text",
            "render_node_card_header",
            "render_services_block",
            "render_node_card_summary",
        ):
            return getattr(mod, fn)(case["arg"])
        if fn == "render_service_card":
            return mod.render_service_card(case["node_name"], case["svc"])
        if fn == "build_node_card_keyboard":
            result = mod.build_node_card_keyboard(
                sub,
                make_actions(ns, case["monitor_actions"]),
                case["service_names"],
                make_actions(ns, case["node_actions"]),
                case["node_id"],
            )
            return {"markup": dump(result)}
        if fn == "build_services_list_view":
            return view(
                mod.build_services_list_view(
                    case["state"],
                    case["node_id"],
                    case["offset"],
                    sub,
                    make_actions(ns, case["node_actions"]),
                )
            )
        if fn == "build_service_card_keyboard":
            result = mod.build_service_card_keyboard(
                sub, make_actions(ns, case["node_actions"]), case["service_name"], case["node_id"]
            )
            return {"markup": dump(result)}
        if fn == "build_node_card_view":
            return view(await mod.build_node_card_view(link, sub, case["node_id"]))
        if fn == "build_services_page_view":
            return view(await mod.build_services_page_view(link, sub, case["node_id"], case["offset"]))
        return view(await mod.build_service_card_view(link, sub, case["service_name"], case["node_id"]))


def drive_actions(case: dict[str, Any], mod: Any, ns: Any) -> Any:
    return asyncio.run(_drive_actions(case, mod, ns))


async def _drive_actions(case: dict[str, Any], mod: Any, ns: Any) -> Any:
    fn = case["fn"]
    link = W2Link(case.get("link"), ns)
    sub = make_sub(ns.Subscription, case.get("sub"))
    if fn == "unavailable_text":
        return mod.unavailable_text(link)
    if fn == "find_action":
        found = await ns.find_action(mod, link, case["service"], case["action_id"], case.get("node_id"))
        return None if found is None else found.to_dict()
    if fn == "build_args":
        return mod.build_args(ns.ActionSpec.from_dict(case["action"]), case["value"])
    if fn == "render_result":
        return mod.render_result(ns.ActionSpec.from_dict(case["action"]), case["result"])
    if fn == "run_action":
        text = await ns.run_action(
            mod, link, case["service"], case["action_id"], case.get("value"), case.get("node_id")
        )
        return {"text": text, "calls": [c for c in link.calls if c[0] == "command"]}
    if fn == "rows":
        buttons = [ns.button(text, data) for text, data in case["buttons"]]
        return {"markup": dump(mod.rows(buttons))}
    if fn == "build_choice_keyboard":
        result = mod.build_choice_keyboard(
            sub, make_actions(ns, case["actions"]), case["service"], case["value"], case.get("node_id")
        )
        return {"markup": dump(result)}
    if fn == "visible_skill_actions":
        return [a.id for a in mod.visible_skill_actions(sub, make_actions(ns, case["actions"]))]
    if fn == "render_app_card":
        return mod.render_app_card(case["app"])
    result = await mod.run_app_skill(link, sub, case["action_id"], case.get("value"))
    return {"view": view(result), "calls": [c for c in link.calls if c[0] == "command"]}


class Notifier:
    def __init__(self, fail: list[int] | None = None) -> None:
        self.fail = set(fail or ())
        self.sent: list[list[Any]] = []

    async def send_direct(self, chat_id: int, text: str, **kwargs: Any) -> int | None:
        self.sent.append([chat_id, text, {k: v for k, v in kwargs.items() if v is not None}])
        return None if chat_id in self.fail else len(self.sent)


class MemState:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)
        self.writes: list[list[str]] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value
        self.writes.append([key, value])


def drive_lifecycle(case: dict[str, Any], mod: Any, ns: Any) -> Any:
    return asyncio.run(_drive_lifecycle(case, mod, ns))


async def _drive_lifecycle(case: dict[str, Any], mod: Any, ns: Any) -> Any:
    fn = case["fn"]
    if fn == "render_startup":
        outage = ns.parse.parse_outage(case["outage"]) if "outage" in case else None
        with local_tz():
            return mod.render_startup(case["clean"], outage)
    if fn == "render_shutdown":
        return mod.render_shutdown()
    if fn == "render_link_restored":
        return mod.render_link_restored(case["seconds"])
    book = ns.Book([make_sub(ns.Subscription, spec) for spec in case["subs"]])
    notifier = Notifier(case.get("fail"))
    state = MemState(case["state"])
    reads: list[int] = []
    outage = ns.parse.parse_outage(case["outage"]) if case.get("outage") else None

    async def read_last_outage() -> Any:
        reads.append(1)
        return outage

    with local_tz():
        if fn == "announce_startup":
            result = await mod.announce_startup(book, notifier, state, read_last_outage)
        else:
            result = await mod.announce_shutdown(book, notifier, state)
    return {
        "clean": result if fn == "announce_startup" else None,
        "sent": notifier.sent,
        "state": state.values,
        "writes": state.writes,
        "outage_reads": len(reads),
    }


def drive_guests(case: dict[str, Any], mod: Any, ns: Any) -> Any:
    fn = case["fn"]
    sub = make_sub(ns.Subscription, case.get("sub"))
    extra = ns.catalog_args(case.get("catalog"))
    with local_tz():
        if fn == "build_list_view":
            return view(
                mod.build_list_view([make_sub(ns.Subscription, g) for g in case["guests"]], case["offset"])
            )
        if fn == "build_codes_view":
            return view(mod.build_codes_view(case["codes"], case["offset"]))
        if fn == "build_card_view":
            return view(mod.build_card_view(sub))
        if fn == "build_kick_confirm_view":
            return view(mod.build_kick_confirm_view(sub))
        if fn == "build_back_to_card_view":
            return view(mod.build_back_to_card_view(case["sub_name"], case["chat_id"], case["body"]))
        if fn == "build_perms_view":
            return view(mod.build_perms_view(sub, case["offset"], *extra))
        if fn == "build_perm_add_view":
            return view(mod.build_perm_add_view(sub, case["offset"], *extra))
        if fn == "granted_rows":
            return mod.granted_rows(sub, *extra)
        return mod.format_local_time(case["iso"])
