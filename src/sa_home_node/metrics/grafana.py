from __future__ import annotations

import re

from sa_home_node.metrics.config import MetricsConfig


def _section_name(line: str) -> str:
    stripped = line.strip()
    if not stripped.startswith("[") or not stripped.endswith("]"):
        return ""
    return stripped[1:-1].strip()


def _is_key_line(line: str, key: str) -> bool:
    return re.match(rf"\s*[;#]?\s*{re.escape(key)}\s*(=.*)?$", line) is not None


def _find_section(lines: list[str], section: str) -> int:
    for index, line in enumerate(lines):
        if _section_name(line) == section:
            return index
    return -1


def _section_bound(lines: list[str], start: int) -> int:
    for index in range(start + 1, len(lines)):
        if _section_name(lines[index]):
            return index
    return len(lines)


def _set_ini_key(lines: list[str], section: str, key: str, value: str) -> None:
    index = _find_section(lines, section)
    if index < 0:
        if lines and lines[-1] == "":
            lines.append(f"[{section}]")
        else:
            lines.append(f"\n[{section}]")
        index = len(lines) - 1
    for position in range(index + 1, _section_bound(lines, index)):
        if _is_key_line(lines[position], key):
            lines[position] = f"{key} = {value}"
            return
    lines.insert(index + 1, f"{key} = {value}")


def set_ini_values(text: str, values: dict[str, dict[str, str]]) -> str:
    lines = text.split("\n") if text else []
    for section, keys in values.items():
        for key, value in keys.items():
            _set_ini_key(lines, section, key, value)
    if not lines or lines[-1] != "":
        lines.append("")
    return "\n".join(lines)


def grafana_ini_values(config: MetricsConfig, host: str) -> dict:
    return {
        "server": {
            "http_addr": host,
            "http_port": str(config.grafana_port),
        },
        "auth.anonymous": {"enabled": "false"},
        "analytics": {
            "reporting_enabled": "false",
            "check_for_updates": "false",
            "check_for_plugin_updates": "false",
        },
        "plugins": {
            "preinstall_disabled": "true",
            "preinstall_auto_update": "false",
        },
    }
