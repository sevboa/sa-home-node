from __future__ import annotations

from datetime import datetime, timedelta
from html import escape

from sa_home_node.monitor.hostmodels import (
    EVENT_HOST_DEGRADED,
    HostEvent,
    fmt_host_value,
)
from sa_home_node.monitor.models import (
    DISK_FAIL,
    DISK_OK,
    DISK_WARN,
    EVENT_OVERHEAT_CLEARED,
    EVENT_OVERHEAT_STARTED,
    EVENT_SMART_DEGRADED,
    KIND_CPU,
    KIND_GPU,
    POWER_CLEAN,
    Event,
    PowerEvent,
    SmartChange,
)

_KIND_WORD_EVENT = {KIND_CPU: "CPU", KIND_GPU: "GPU"}


def _kind_word(kind: str) -> str:
    return _KIND_WORD_EVENT.get(kind, "Диск")


def _format_local(dt: datetime, fmt: str) -> str:
    return dt.astimezone().strftime(fmt)


def _format_dhms(td: timedelta) -> str:
    total = max(int(td.total_seconds()), 0)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, rem = divmod(rem, 60)
    parts = [
        f"{value}{word}" for value, word in ((days, "d"), (hours, "h"), (minutes, "m"), (rem, "s")) if value
    ]
    return " ".join(parts) if parts else "0s"


def render_event(event: Event) -> str:
    label = escape(event.label)
    temp = f"{event.temperature_c:.1f}°C"
    when = _format_local(event.at, "%H:%M:%S")
    kind = _kind_word(event.kind)
    body = f"\nТемпература: <b>{temp}</b>\nВремя: {when}"
    if event.type == EVENT_OVERHEAT_STARTED:
        return f"🔥 <b>Перегрев</b> — {kind} «{label}»{body}"
    if event.type == EVENT_OVERHEAT_CLEARED:
        return f"✅ <b>Норма</b> — {kind} «{label}» остыл{body}"
    return f"ℹ️ {kind} «{label}»: {temp} ({when})"


def render_host_event(event: HostEvent) -> str:
    label = escape(event.label)
    val = fmt_host_value(event.value, event.unit)
    when = _format_local(event.at, "%H:%M:%S")
    if event.type == EVENT_HOST_DEGRADED:
        hint = f" — {escape(event.hint)}" if event.hint else ""
        return f"⚠️ <b>{label}: {val}</b>{hint}\nВремя: {when}"
    return f"✅ <b>{label}</b> вернулся к норме ({val})\nВремя: {when}"


_HEALTH_WORD = {DISK_OK: "норма", DISK_WARN: "предупреждение", DISK_FAIL: "СБОЙ"}


def _health_word(health: str | None) -> str:
    return _HEALTH_WORD.get(health, "н/д")


def render_smart_change(change: SmartChange) -> str:
    label = escape(change.label)
    if change.event_type == EVENT_SMART_DEGRADED:
        lines = [f"⚠️ <b>SMART: ухудшение</b> — диск «{label}»"]
    else:
        lines = [f"✅ <b>SMART: улучшение</b> — диск «{label}»"]
    if change.health_to != change.health_from:
        lines.append(
            f"Здоровье: {_health_word(change.health_from)} → <b>{_health_word(change.health_to)}</b>"
        )
    for c in change.attr_changes:
        arrow = "🔺" if c.new > c.old else "🔻"
        lines.append(f"{arrow} {escape(c.name)}: {c.old} → <b>{c.new}</b>")
    return "\n".join(lines)


def render_outage_line(e: PowerEvent) -> str:
    icon = "🔌" if e.kind == POWER_CLEAN else "⚡"
    down = _format_local(e.down_at, "%m/%d %H:%M") if e.down_at else "?"
    up = _format_local(e.up_at, "%m/%d %H:%M") if e.up_at else "?"
    tail = "" if e.downtime is None else f" ({_format_dhms(e.downtime)})"
    return f"{icon} {down} - {up}{tail}"
