from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from sa_home_node.bot import actions, nodelinks, nodeview
from sa_home_node.bot.commands import wake_callback
from sa_home_node.bot.db import AppState
from sa_home_node.bot.servicelink import ServiceLink
from sa_home_node.bot.subs import Subscription
from sa_home_node.bot.ui import button, format_duration
from sa_home_node.bot.wakestate import StateWakeCache
from sa_home_node.monitor.models import KIND_CPU, KIND_GPU, POWER_UNEXPECTED, PowerEvent
from sa_home_node.services.periodic import PEER_ERRORS
from sa_home_node.swarm.kind import traits_for
from sa_home_node.wake.peers import NodeReport, collect_reports

log = logging.getLogger(__name__)

SWARM_HEADER = "🕸 <b>Рой</b>"

REMOTE_STUB_TEXT = "💻 <b>Домашний ПК</b> — вне роя (нода ещё не развёрнута), Wake-on-LAN"

WAKE_BUTTON_TEXT = "🔌 Разбудить ПК"

LAMP_GREEN = "🟢"
LAMP_RED = "🔴"
LAMP_GRAY = "⚪"

SERVICE_CALL_ERRORS = PEER_ERRORS


def wake_node_text(node_id: str) -> str:
    return f"🔌 Разбудить {node_id}"


def _temp_max(monitor: dict[str, Any], kind: str) -> float | None:
    temps = [
        h.get("temperature_c")
        for h in monitor.get("health", [])
        if h.get("kind") == kind and h.get("temperature_c") is not None
    ]
    return max(temps) if temps else None


def _health_bucket(report: NodeReport) -> int:
    if not report.alive:
        if report.left:
            return 2
        return 0 if traits_for(report.kind).always_on else 2
    if report.state is None:
        return 0
    services = report.state.get("services", [])
    if any(s.get("status") != "running" for s in services):
        return 0
    monitor = report.monitor
    if monitor is None:
        return 0
    if any(h.get("status") == "alerting" for h in monitor.get("health", [])):
        return 0
    if monitor.get("requirements"):
        return 0
    return 1


def order_by_health(reports: Sequence[NodeReport]) -> list[NodeReport]:
    return sorted(reports, key=_health_bucket)


def node_line(report: NodeReport) -> str:
    name = nodelinks.node_command(report.node_id) or f"<b>{report.node_id}</b>"
    if not report.alive:
        if report.left:
            return f"{LAMP_GRAY} {name} — выключена штатно"
        if not traits_for(report.kind).always_on:
            return f"{LAMP_GRAY} {name} — спит (это норма)"
        return f"{LAMP_RED} {name} — не в сети"
    if report.state is None:
        return f"{LAMP_RED} {name} — не отвечает"

    bits = [f"v{report.state.get('version', '?')}"]
    services = report.state.get("services", [])
    running = sum(1 for s in services if s.get("status") == "running")
    bits.append(f"службы {running}/{len(services)}")

    if report.monitor is None:
        bits.append("монитор не отвечает")
    else:
        cpu = _temp_max(report.monitor, KIND_CPU)
        if cpu is not None:
            bits.append(f"CPU {cpu:.0f}°C")
        gpu = _temp_max(report.monitor, KIND_GPU)
        if gpu is not None:
            bits.append(f"GPU {gpu:.0f}°C")
        alerting = sum(1 for h in report.monitor.get("health", []) if h.get("status") == "alerting")
        if alerting:
            bits.append(f"🔔 {alerting}")
        if report.monitor.get("requirements"):
            bits.append("⚠️")
    return f"{LAMP_GREEN} {name} · " + " · ".join(bits)


def node_versions(reports: Sequence[NodeReport]) -> dict[str, str]:
    return {r.node_id: r.state["version"] for r in reports if r.state is not None and r.state.get("version")}


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def latest_version(versions: dict[str, str]) -> str:
    return max(versions.values(), key=_version_key)


def _versions_line(reports: Sequence[NodeReport]) -> str | None:
    versions = node_versions(reports)
    if not versions:
        return None
    latest = latest_version(versions)
    stale = {nid: v for nid, v in versions.items() if v != latest}
    if not stale:
        return f"ПО: v{latest} у всех"
    lagging = ", ".join(f"{nid} (v{v})" for nid, v in sorted(stale.items()))
    return f"ПО: свежая v{latest} · отстаёт {lagging}"


def _parse_outage_safe(raw: dict[str, Any] | None) -> PowerEvent | None:
    if raw is None:
        return None
    kind = raw.get("kind")
    if kind != POWER_UNEXPECTED:
        return None
    boot_at_raw = raw.get("boot_at")
    down_at_raw = raw.get("down_at")
    up_at_raw = raw.get("up_at")
    if boot_at_raw is None and down_at_raw is None:
        return None
    try:
        boot_at = datetime.fromisoformat(boot_at_raw) if boot_at_raw else None
        down_at = datetime.fromisoformat(down_at_raw) if down_at_raw else None
        up_at = datetime.fromisoformat(up_at_raw) if up_at_raw else None
    except (ValueError, TypeError):
        return None
    return PowerEvent(
        kind=kind,
        boot_at=boot_at or down_at or datetime.now(UTC),
        down_at=down_at,
        up_at=up_at,
        down_approx=bool(raw.get("down_approx", False)),
    )


def last_failure_line(reports: list[NodeReport], now: datetime) -> str | None:
    freshest: tuple[datetime, str, PowerEvent] | None = None
    for r in reports:
        if r.monitor is None:
            continue
        outage = _parse_outage_safe(r.monitor.get("last_outage"))
        if outage is None:
            continue
        moment = outage.down_at or outage.boot_at
        if freshest is None or moment > freshest[0]:
            freshest = (moment, r.node_id, outage)
    if freshest is None:
        return None
    moment, node_id, _ = freshest
    ago = format_duration((now - moment).total_seconds())
    return f"Последний сбой: {node_id}, {ago} назад"


def render_swarm(reports: list[NodeReport], wake: object | None, now: datetime) -> str:
    total = len(reports)
    online = sum(1 for r in reports if r.alive and r.state is not None)
    lines = [f"{SWARM_HEADER}: {total} нод, в сети {online}"]
    for extra in (_versions_line(reports), last_failure_line(reports, now)):
        if extra:
            lines.append(extra)
    lines.append("")
    lines.extend(node_line(r) for r in order_by_health(reports))
    if wake is not None and getattr(wake, "mac", None):
        lines.append(REMOTE_STUB_TEXT)
    return "\n".join(lines)


def wake_rows(subscription: Subscription, wake: object | None) -> list[InlineKeyboardButton]:
    if wake is None or not getattr(wake, "mac", None) or not subscription.allows_command("wake"):
        return []
    return [button(WAKE_BUTTON_TEXT, wake_callback())]


async def offline_wakeable_ids(
    subscription: Subscription, state: AppState, reports: Sequence[NodeReport]
) -> list[str]:
    if not subscription.allows_command("wake"):
        return []
    ids: list[str] = []
    for r in reports:
        if r.alive and r.state is not None:
            continue
        if r.kind and not traits_for(r.kind).wakeable:
            continue
        if await StateWakeCache(state).get(r.node_id) is None:
            continue
        ids.append(r.node_id)
    return ids


async def offline_wake_rows(
    subscription: Subscription, state: AppState, reports: Sequence[NodeReport]
) -> list[InlineKeyboardButton]:
    ids = await offline_wakeable_ids(subscription, state, reports)
    return [button(wake_node_text(node_id), wake_callback(node_id)) for node_id in ids]


async def remember_wake_info(state: AppState, reports: Sequence[NodeReport]) -> None:
    cache = StateWakeCache(state)
    for r in reports:
        if r.state is not None:
            await cache.put(r.node_id, r.state.get("wake"))


async def build_swarm_keyboard(
    subscription: Subscription | None,
    wake: object | None,
    reports: Sequence[NodeReport] = (),
    state: AppState | None = None,
) -> InlineKeyboardMarkup | None:
    if subscription is None:
        return None
    buttons = wake_rows(subscription, wake)
    if state is not None:
        buttons = buttons + await offline_wake_rows(subscription, state, reports)
    if not buttons:
        return None
    return actions.rows(buttons)


async def build_swarm_view(
    node_link: ServiceLink,
    subscription: Subscription | None,
    wake: object | None = None,
    state: AppState | None = None,
) -> tuple[str, InlineKeyboardMarkup | None]:
    try:
        own_state = await node_link.get_state()
    except SERVICE_CALL_ERRORS:
        return nodeview.NODE_DOWN_TEXT, None
    reports = await collect_reports(node_link, own_state, with_monitor=True)
    if state is not None:
        await remember_wake_info(state, reports)
    text = render_swarm(reports, wake, datetime.now(UTC))
    keyboard = await build_swarm_keyboard(subscription, wake, reports, state)
    return text, keyboard
