from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from typing import Any

from sa_home_node.bot.render_alerts import render_outage_line
from sa_home_node.monitor.hostmodels import (
    HostMetricState,
    fmt_host_value,
)
from sa_home_node.monitor.models import (
    ALERTING,
    DISK_FAIL,
    DISK_OK,
    DISK_WARN,
    KIND_CPU,
    KIND_GPU,
    DiskSummary,
    HealthState,
    PowerEvent,
)


def _format_local(dt: datetime, fmt: str) -> str:
    return dt.astimezone().strftime(fmt)


_KIND_WORD_EVENT = {KIND_CPU: "CPU", KIND_GPU: "GPU"}


def _kind_word(kind: str) -> str:
    return _KIND_WORD_EVENT.get(kind, "Диск")


def render_host_line(state: HostMetricState) -> str:
    mark = "⚠️ " if state.status == ALERTING else ""
    short = _HOST_SHORT_LABEL.get(state.metric, state.label)
    val = fmt_host_value(state.value, state.unit)
    suffix = ""
    if state.status == ALERTING and state.alerting_since is not None:
        suffix = f" (с {_format_local(state.alerting_since, '%H:%M')})"
    return f" • {mark}{short}: <b>{val}</b>{suffix}"


_HOST_CARD_METRICS = (
    "steal_pct",
    "iowait_pct",
    "load_per_core",
    "mem_available_pct",
    "swap_used_pct",
    "disk_used_pct",
    "conntrack_pct",
)
_HOST_SHORT_LABEL = {
    "steal_pct": "CPU steal",
    "iowait_pct": "iowait (нагрузка на диск)",
    "load_per_core": "load / ядро",
    "mem_available_pct": "RAM свободно",
    "swap_used_pct": "swap занято",
    "disk_used_pct": "диск / заполнен",
    "psi_cpu": "PSI cpu",
    "psi_memory": "PSI mem",
    "psi_io": "PSI io",
    "oom_kills": "OOM-kill",
    "conntrack_pct": "conntrack заполнен",
    "net_err_rate": "ошибки/дропы NIC",
    "kernel_stall_events": "стойла ядра",
}


def render_host_block(states: list[HostMetricState]) -> str:
    if not states:
        return ""
    by_metric = {s.metric: s for s in states}
    alerting = [s for s in states if s.status == ALERTING]
    lines = [f"{'⚠️' if alerting else '📊'} <b>Хост VPS</b>"]
    shown: set[str] = set()
    for metric in _HOST_CARD_METRICS:
        st = by_metric.get(metric)
        if st is not None:
            lines.append(render_host_line(st))
            shown.add(metric)
    for st in alerting:
        if st.metric not in shown:
            lines.append(render_host_line(st))
    return "\n".join(lines)


_HEALTH_WORD = {DISK_OK: "норма", DISK_WARN: "предупреждение", DISK_FAIL: "СБОЙ"}


def _health_word(health: str | None) -> str:
    return _HEALTH_WORD.get(health, "н/д")


def render_state_line(state: HealthState) -> str:
    icon = "🔥" if state.status == ALERTING else "✅"
    label = escape(state.label)
    temp = f"{state.temperature_c:.1f}°C"
    kind = _kind_word(state.kind)
    suffix = ""
    if state.status == ALERTING and state.alerting_since is not None:
        suffix = f" с {_format_local(state.alerting_since, '%H:%M:%S')}"
    return f"{icon} {kind} «{label}»: <b>{temp}</b>{suffix}"


def _format_largest_unit(td: timedelta) -> str:
    total = max(int(td.total_seconds()), 0)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} d"
    if hours:
        return f"{hours} h"
    if minutes:
        return f"{minutes} m"
    return "&lt;1 m"


def render_downtime(events: list[PowerEvent], offset: int = 0) -> str:
    if not events:
        if offset:
            return "Дальше отключений нет."
        return "Нет данных об отключениях (журнал `last` пуст или недоступен)."
    header = (
        "<b>Последние отключения</b>"
        if offset == 0
        else f"<b>Отключения {offset + 1}–{offset + len(events)}</b>"
    )
    lines = [header, ""]
    lines.extend(render_outage_line(e) for e in events)
    return "\n".join(lines)


def render_status_full(states: list[HealthState]) -> str:
    if not states:
        return "Пока нет данных — сканер ещё не снимал срез."
    alerting = [s for s in states if s.status == ALERTING]
    header = "🔥 <b>Есть перегрев!</b>" if alerting else "✅ <b>Всё в норме.</b>"
    lines = [header, ""]
    lines.extend(render_state_line(s) for s in states)
    return "\n".join(lines)


_DISK_ICON = {DISK_OK: "✅", DISK_WARN: "⚠️", DISK_FAIL: "❌"}

_DISK_TEMP_COLD_C = 25.0
_CPU_TEMP_COLD_C = 35.0
_GPU_TEMP_COLD_C = 40.0


def _temp_mood(temp_c: float, cold: float, warn_c: float, crit_c: float) -> str:
    if temp_c >= crit_c:
        return "🔥"
    if temp_c >= warn_c:
        return "🥵"
    if temp_c >= cold:
        return "🙂"
    return "🥶"


def _disk_temp_mood(temp_c: float, warn_c: float, crit_c: float) -> str:
    return _temp_mood(temp_c, _DISK_TEMP_COLD_C, warn_c, crit_c)


def _cpu_temp_mood(temp_c: float, warn_c: float, crit_c: float) -> str:
    return _temp_mood(temp_c, _CPU_TEMP_COLD_C, warn_c, crit_c)


def _gpu_temp_mood(temp_c: float, warn_c: float, crit_c: float) -> str:
    return _temp_mood(temp_c, _GPU_TEMP_COLD_C, warn_c, crit_c)


def _fmt_gb(nbytes: int) -> str:
    return f"{nbytes / 1e9:.0f}"


def _disk_usage(d: DiskSummary) -> str:
    if not d.total_bytes:
        return "не смонтирован"
    used = d.total_bytes - (d.free_bytes or 0)
    pct = round(used / d.total_bytes * 100)
    return f"{_fmt_gb(used)} из {_fmt_gb(d.total_bytes)} ГБ ({pct}%)"


_KIND_WORD = {"hdd": "HDD", "ssd": "SSD", "nvme": "NVMe", "emmc": "eMMC"}


def render_disk_line(d: DiskSummary, warn_c: float, crit_c: float) -> str:
    word = _KIND_WORD.get(d.kind, "HDD")
    is_emmc = d.kind == "emmc"
    heading = word if is_emmc else f"{word} {escape(d.model) if d.model else '?'}"
    icon = "" if is_emmc else _DISK_ICON.get(d.health, "❔") + " "
    usage = _disk_usage(d)
    if d.temperature_c is None:
        return f"{icon}{heading}: {usage}"
    mood = _disk_temp_mood(d.temperature_c, warn_c, crit_c)
    return f"{icon}{heading}\n {d.temperature_c:.0f}°C {mood}| {usage}"


def render_gpu_line(state: HealthState, warn_c: float, crit_c: float) -> str:
    label = escape(state.label)
    mood = _gpu_temp_mood(state.temperature_c, warn_c, crit_c)
    icon = "🔥" if state.status == ALERTING else "✅"
    return f"{icon} GPU {label}: {state.temperature_c:.1f}°C {mood}"


def render_status_summary(
    now: datetime,
    uptime: timedelta | None,
    cpu_states: list[HealthState],
    gpu_states: list[HealthState],
    disks: list[DiskSummary],
    last_outage: PowerEvent | None,
    cpu_warn_c: float,
    cpu_crit_c: float,
    gpu_warn_c: float,
    gpu_crit_c: float,
    disk_warn_c: float,
    disk_crit_c: float,
) -> str:
    lines = [_format_local(now, "%Y-%m-%d %H:%M")]
    if uptime is not None:
        lines.append(f"uptime: {_format_largest_unit(uptime)}")
    if last_outage is not None:
        lines.append(f"last off: {render_outage_line(last_outage)}")

    body: list[str] = []
    cpu = [s for s in cpu_states if s.kind == KIND_CPU]
    if cpu:
        hot = any(s.status == ALERTING for s in cpu)
        tmax = max(s.temperature_c for s in cpu)
        mood = _cpu_temp_mood(tmax, cpu_warn_c, cpu_crit_c)
        body.append(f"{'🔥' if hot else '✅'} CPU: {tmax:.1f}°C {mood}")
    gpu = [s for s in gpu_states if s.kind == KIND_GPU]
    body.extend(render_gpu_line(g, gpu_warn_c, gpu_crit_c) for g in gpu)
    body.extend(render_disk_line(d, disk_warn_c, disk_crit_c) for d in disks)

    if body:
        lines.append("")
        lines.extend(body)
    return "\n".join(lines)


def _format_run_time(value: str | datetime) -> str:
    if isinstance(value, datetime):
        return _format_local(value, "%m-%d %H:%M:%S")
    try:
        return _format_local(datetime.fromisoformat(value), "%m-%d %H:%M:%S")
    except ValueError:
        return str(value)


def _fmt_run(run: dict[str, Any]) -> str:
    icon = {"ok": "✅", "error": "❌", "running": "⏳"}.get(run["status"], "•")
    started = _format_run_time(run["started_at"])
    return f"{icon} {run['job_type']} @ {started}"


def render_stats(counts: dict[str, int], runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "Прогонов сканера ещё не было."
    total = sum(counts.values())
    total_line = (
        f"Всего прогонов: {total} (ok={counts.get('ok', 0)}, error={counts.get('error', 0)}, "
        f"running={counts.get('running', 0)})"
    )
    lines = [
        "<b>Статистика сканера</b>",
        total_line,
        "",
        "Последние:",
    ]
    lines.extend(_fmt_run(r) for r in runs)
    return "\n".join(lines)
