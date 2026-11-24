from __future__ import annotations

import re
import tomllib
from collections.abc import Sequence
from typing import Any

from sa_home_node.toml_emit import toml_tables

_HEADER = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")


def patch_toml_sections(text: str, name: str, values: dict[str, Any], *, keep: Sequence[str] = ()) -> str:
    merged = dict(values)
    old = (tomllib.loads(text).get(name) or {}) if text.strip() else {}
    for key in keep:
        if key in old:
            merged[key] = old[key]
        else:
            merged.pop(key, None)
    kept: list[str] = []
    skipping = False
    for line in text.splitlines():
        match = _HEADER.match(line)
        if match:
            skipping = match.group(1) == name or match.group(1).startswith(f"{name}.")
        if not skipping:
            kept.append(line)
    head = "\n".join(kept).rstrip("\n")
    return (head + "\n\n" if head else "") + toml_tables(name, merged)
