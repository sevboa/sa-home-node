from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "bot"


def load(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))
