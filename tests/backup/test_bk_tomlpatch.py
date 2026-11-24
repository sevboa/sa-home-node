from __future__ import annotations

import json
from pathlib import Path

import pytest

from sa_home_node.backup.tomlpatch import patch_toml_sections

CASES = json.loads((Path(__file__).parents[1] / "fixtures" / "backup" / "tomlpatch.json").read_text(encoding="utf-8"))
LOCAL_KEYS = next(c["keys"] for c in CASES if c["name"] == "local_keys")
LOSSY = {"list_of_dicts"}
PATCH_CASES = [c for c in CASES if "identity" in c and c["name"] not in LOSSY]


def run(case):
    config = case["identity"].get("config") or {}
    values = dict(config.get("svc") or {})
    sub = config.get("svc_sub")
    if sub is not None:
        values["sub"] = sub
    return patch_toml_sections(case["text"], "svc", values, keep=LOCAL_KEYS)


def test_list_of_dicts_refused():
    case = next(c for c in CASES if c["name"] in LOSSY)
    with pytest.raises(ValueError, match="список словарей"):
        run(case)


@pytest.mark.parametrize("case", PATCH_CASES, ids=lambda c: c["name"])
def test_patch_case(case):
    if "error" in case:
        with pytest.raises(Exception) as info:
            run(case)
        assert type(info.value).__name__ == case["error"]["type"]
        assert str(info.value) == case["error"]["message"]
        return
    assert run(case) == case["result"]
