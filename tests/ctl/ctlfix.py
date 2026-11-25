from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "ctl"


def load(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@contextlib.contextmanager
def zone(name: str) -> Iterator[None]:
    saved = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        time.tzset()
