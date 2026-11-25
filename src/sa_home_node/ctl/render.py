from __future__ import annotations

from datetime import datetime

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


def render_update_result(result: dict) -> str:
    if result.get("up_to_date"):
        return f"Уже последняя версия v{result.get('version', '?')}."
    target = result.get("target_version", "?")
    return (
        f"Запущено обновление до v{target} в фоне (файлы на диске, процесс не тронут). "
        f"После завершения — nodectl restart_node."
    )
