from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from sa_home_node import cli


class FakeHost:
    pass


@pytest.fixture
def init(monkeypatch, tmp_path):
    recorded: dict = {}

    def fake_run_init(args, **kwargs):
        recorded.update(kwargs, args=args)
        return 42

    monkeypatch.setattr(cli, "run_init", fake_run_init)
    monkeypatch.setattr(cli, "current", FakeHost)
    monkeypatch.setattr(sys, "argv", ["/custom/bin/sa-home-node", "init"])

    def call(*argv: str, tty: bool = False) -> int:
        monkeypatch.setattr(sys.stdin, "isatty", lambda: tty)
        return cli.main(["init", "--config", str(tmp_path / "cfg.toml"), *argv])

    return call, recorded


def test_main_passes_dependencies(init):
    call, recorded = init
    assert call() == 42
    assert recorded["reply"] is input
    assert isinstance(recorded["host"], FakeHost)
    assert recorded["executable"] == str(Path("/custom/bin/sa-home-node").resolve())
    assert recorded["args"].command == "init"


@pytest.mark.parametrize(
    ("argv", "tty", "expected"),
    [((), True, True), ((), False, False), (("--non-interactive",), True, False)],
)
def test_interactive(init, argv, tty, expected):
    call, recorded = init
    call(*argv, tty=tty)
    assert recorded["interactive"] is expected


def test_run_step(init, monkeypatch):
    call, recorded = init
    call()
    calls = []
    codes = iter([0, 1])
    monkeypatch.setattr(
        subprocess, "run", lambda argv, check: calls.append(argv) or SimpleNamespace(returncode=next(codes))
    )
    assert recorded["run_step"](["cmd", "arg"]) is True
    assert recorded["run_step"](["fail"]) is False
    assert calls == [["cmd", "arg"], ["fail"]]


def test_fix(init, monkeypatch, tmp_path):
    call, recorded = init
    call()
    seen = []
    monkeypatch.setattr(cli, "fix_command", lambda path, **kw: seen.append((path, kw)) or 3)
    assert recorded["fix"](tmp_path / "x.toml") == 3
    assert seen == [(tmp_path / "x.toml", {"dry_run": False, "only": (), "list_only": False})]
