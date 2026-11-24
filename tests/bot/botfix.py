from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiogram.types import InlineKeyboardMarkup

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "bot"


def load(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def inflate(value: Any) -> Any:
    if isinstance(value, dict) and len(value) == 1:
        ((key, body),) = value.items()
        if key == "parts":
            return "".join(char * count for char, count in body)
        if key == "bytes":
            return b"x" * body
        if key == "markup":
            return InlineKeyboardMarkup.model_validate(body)
    if isinstance(value, list):
        return [inflate(item) for item in value]
    return value
