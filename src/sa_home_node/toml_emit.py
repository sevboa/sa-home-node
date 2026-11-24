from __future__ import annotations

import json
import re
from typing import Any

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list):
        if any(isinstance(item, dict) for item in value):
            raise ValueError("список словарей не выражается значением TOML")
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    return toml_string(str(value))


def _key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else toml_string(key)


def toml_table(name: str, values: dict[str, Any]) -> str:
    body = [f"{_key(k)} = {toml_value(v)}" for k, v in values.items() if v is not None]
    return "\n".join([f"[{name}]", *body]) + "\n"


def toml_tables(name: str, values: dict[str, Any]) -> str:
    return "\n".join(_tables(name, values))


def _tables(name: str, values: dict[str, Any]) -> list[str]:
    scalars = {k: v for k, v in values.items() if not isinstance(v, dict)}
    parts = [toml_table(name, scalars)]
    for key, value in values.items():
        if isinstance(value, dict):
            parts += _tables(f"{name}.{_key(key)}", value)
    return parts
