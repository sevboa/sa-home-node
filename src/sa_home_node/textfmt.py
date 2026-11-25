from __future__ import annotations


def format_duration(seconds: float) -> str:
    total = max(int(seconds), 0)
    parts: list[str] = []
    for name, value in (("д", 86400), ("ч", 3600), ("м", 60)):
        unit = total // value
        if unit:
            parts.append(f"{unit}{name}")
        total %= value
    if total or not parts:
        parts.append(f"{total}с")
    return " ".join(parts)
