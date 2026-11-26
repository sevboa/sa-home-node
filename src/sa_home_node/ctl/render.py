from __future__ import annotations

from datetime import datetime

from sa_home_node.proto.wire import Envelope
from sa_home_node.swarm.peerpath import format_peer_path
from sa_home_node.textfmt import format_duration

_STATUS_ICON = {"running": "🟢", "restarting": "🟠", "stopped": "🔴"}


def _fmt_started(iso: str | None) -> str:
    if not iso:
        return "—"
    return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def render_services(services: list[dict]) -> str:
    if not services:
        return "Службы не назначены."
    rows = [("СЛУЖБА", "СОСТОЯНИЕ", "PID", "РЕСТАРТЫ", "ЗАПУЩЕНА")]
    for svc in services:
        icon = _STATUS_ICON.get(svc.get("status", ""), "⚪")
        name = svc.get("name", "?")
        if svc.get("external"):
            name += " (внешняя)"
        rows.append(
            (
                name,
                f"{icon} {svc.get('status', '?')}",
                str(svc.get("pid") or "—"),
                str(svc.get("restarts", 0)),
                _fmt_started(svc.get("started_at")),
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows)


def render_check_update(result: dict) -> str:
    lines = [
        f"Репозиторий: {result.get('repo', '?')}",
        f"Работает: v{result.get('running', '?')}",
        f"На диске: v{result.get('installed') or '?'}",
        f"В репозитории: v{result.get('latest', '?')}",
    ]
    if result.get("installed") == result.get("latest"):
        lines.append("Установлена последняя версия.")
    else:
        lines.append("Доступно обновление — nodectl update.")
    return "\n".join(lines)


def _render_peer(peer: dict) -> str:
    icon = "🟢" if peer.get("alive") else "🔴"
    line = f"  {icon} {peer.get('id', '?')} ({peer.get('endpoint', '?')})"
    path = format_peer_path(peer)
    return f"{line} · {path}" if path else line


def render_status(state: dict) -> str:
    header = f"Нода {state.get('node', '?')} (v{state.get('version', '?')})"
    uptime_bits = []
    if state.get("system_uptime_s") is not None:
        uptime_bits.append(f"система {format_duration(state['system_uptime_s'])}")
    if state.get("uptime_s") is not None:
        uptime_bits.append(f"нода {format_duration(state['uptime_s'])}")
    if uptime_bits:
        header += "\nАптайм: " + " · ".join(uptime_bits)
    out = header + "\n" + render_services(state.get("services", []))
    peers = state.get("peers") or []
    if peers:
        lines = [_render_peer(p) for p in peers]
        out += "\nПиры:\n" + "\n".join(lines)
    update = state.get("update")
    if update and update.get("restart_required"):
        out += f"\n⚠️ Обновлено до v{update.get('installed')} — требуется nodectl restart_node"
    return out


def render_event(env: Envelope, now: datetime) -> str:
    name = env.payload.get("event", "?")
    data = env.payload.get("data", {})
    details = " ".join(f"{k}={v}" for k, v in data.items())
    stamp = now.strftime("%H:%M:%S")
    return f"{stamp} {name} {details}".rstrip()


def render_update_result(result: dict) -> str:
    if result.get("up_to_date"):
        return f"Уже последняя версия v{result.get('version', '?')}."
    target = result.get("target_version", "?")
    return (
        f"Запущено обновление до v{target} в фоне (файлы на диске, процесс не тронут). "
        f"После завершения — nodectl restart_node."
    )
