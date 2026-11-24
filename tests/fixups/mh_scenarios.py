from __future__ import annotations

import json
import socket
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from sa_home_node import promexport
from sa_home_node.config import NodeConfig, NodeSection
from sa_home_node.fixups.core import Fixup, FixupContext, FixupError
from sa_home_node.host import select as select_host

FIXTURES = Path(__file__).parent.parent / "fixtures" / "metrics" / "fixups"
USER = "user-a"
TMP_MARK = "<tmp>"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_ctx(tmp_path: Path, config: dict, plugins: tuple = ()) -> FixupContext:
    node = NodeSection(id="n1", state_path=str(tmp_path / "state.json"))
    node_config = NodeConfig(path=tmp_path / "config.toml", node=node, sections={"metrics": config})
    return FixupContext.build(node_config, select_host("linux"), plugins)


def scenario_params(runs: dict) -> list:
    return [
        pytest.param(group["id"], scenario, id=f"{group['id']}-{scenario['name']}")
        for group in runs["fixups"]
        for scenario in group["scenarios"]
    ]


def by_id(fixups: list[Fixup], fixup_id: str) -> Fixup:
    return next(fixup for fixup in fixups if fixup.id == fixup_id)


def _normalize(calls: list[list[str]]) -> list[list[str]]:
    prefix = tempfile.gettempdir() + "/"
    return [[TMP_MARK if arg.startswith(prefix) else arg for arg in call] for call in calls]


def _set_overlay(monkeypatch: pytest.MonkeyPatch, address: str | None) -> None:
    ifaces = {"tun0": [SimpleNamespace(family=socket.AF_INET, address=address)]} if address else {}
    monkeypatch.setattr(promexport.psutil, "net_if_addrs", lambda: ifaces)
    monkeypatch.setattr(promexport, "is_tun", lambda iface: iface == "tun0")


def play(fixup: Fixup, root, setup: dict, monkeypatch: pytest.MonkeyPatch) -> dict:
    root.files.update(setup.get("files", {}))
    root.dirs.update(setup.get("dirs", []))
    root.active_units.update(setup.get("active_units", []))
    root.missing_tools.update(setup.get("missing_tools", []))
    monkeypatch.setenv("LOGNAME", USER)
    if "overlay_address" in setup:
        _set_overlay(monkeypatch, setup["overlay_address"])
    try:
        before = fixup.check()
    except FixupError as exc:
        return {"error": str(exc)}
    if before:
        return {"check_before": True}
    root.calls.clear()
    try:
        fixup.apply()
    except FixupError as exc:
        return {"check_before": False, "error": str(exc)}
    return {
        "check_before": False,
        "sudo_calls": _normalize(root.sudo_calls),
        "downloads": _normalize([call for call in root.calls if call[0] == "curl"]),
        "installed": dict(root.installed),
        "check_after": fixup.check(),
    }
