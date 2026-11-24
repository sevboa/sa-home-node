from __future__ import annotations

import json
from pathlib import Path

import pytest

from sa_home_node.backup import privio
from sa_home_node.fixups import privileged

FIXTURE = Path(__file__).parents[1] / "fixtures" / "backup" / "apply.json"


@pytest.fixture
def calls(monkeypatch):
    log = []
    monkeypatch.setattr(privileged, "run_sudo", lambda argv: log.append(("sudo", list(argv))))
    monkeypatch.setattr(privileged, "run_user", lambda argv, **kw: log.append(("user", list(argv))))
    monkeypatch.setattr(privileged, "install_from", lambda src, dest, *, mode, make_parents=False: log.append(
        ("install", Path(src).read_text(encoding="utf-8"), str(dest), mode, make_parents)
    ))
    return log


def test_write_root(calls):
    privio.SudoIO().write_root(Path("/etc/x/y.toml"), "текст")
    assert calls == [("install", "текст", "/etc/x/y.toml", "0600", True)]


@pytest.mark.parametrize(
    ("user", "expected"),
    [
        (True, ("user", ["systemctl", "--user", "restart", "u.service"])),
        (False, ("sudo", ["systemctl", "restart", "u.service"])),
    ],
)
def test_restart(calls, user, expected):
    privio.SudoIO().restart("u.service", user=user)
    assert calls == [expected]


def test_read_and_active(monkeypatch):
    monkeypatch.setattr(privileged, "read_text_if_exists", lambda p: f"r:{p}")
    seen = []
    monkeypatch.setattr(privileged, "unit_is_active", lambda unit, *, user=False: seen.append((unit, user)) or True)
    io = privio.SudoIO()
    assert io.read_root(Path("/a")) == "r:/a"
    assert io.unit_active("u", user=True) is True
    assert seen == [("u", True)]


@pytest.mark.skipif(not FIXTURE.exists(), reason="нет эталона")
def test_fixture(calls):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data
