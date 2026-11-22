from __future__ import annotations

from types import SimpleNamespace

import pytest

from sa_home_node.fixups import autoupdate
from sa_home_node.fixups.core import FixupError, run_fixups

REPO = "https://example.org/user-a/node.git"
QUERY = f"schtasks /Query /TN {autoupdate.TASK_NAME}"


@pytest.fixture
def env(root, tmp_path, monkeypatch):
    monkeypatch.setattr(autoupdate, "origin_repo_url", lambda: REPO)
    monkeypatch.setattr(autoupdate, "installed_extras", lambda: "windows")
    monkeypatch.setattr(autoupdate, "pipx_home", lambda: "C:\\pipx")
    ctx = SimpleNamespace(config=SimpleNamespace(path=tmp_path / "node" / "config.toml"))
    fixup = autoupdate.autoupdate_fixups(ctx)[0]
    script = tmp_path / "node" / "service" / autoupdate.SCRIPT_NAME
    return SimpleNamespace(root=root, fixup=fixup, script=script)


def test_declaration(env):
    assert env.fixup.id == "node-autoupdate-task"
    assert env.fixup.platforms == ("windows",)


def test_apply_writes_script_and_registers_task(env):
    assert not env.fixup.check()
    env.fixup.apply()
    text = env.script.read_bytes().decode("utf-8-sig")
    assert "\r\n" in text
    assert f"$RepoUrl = '{REPO}'" in text
    assert "$Spec = 'sa-home-node[windows] @ git+" + REPO + "'" in text
    assert "$PipxHome = 'C:\\pipx'" in text
    assert "@" + "REPO@" not in text
    create = next(c for c in env.root.calls if c[:2] == ["schtasks", "/Create"])
    assert create[create.index("/TN") + 1] == autoupdate.TASK_NAME
    assert str(env.script) in create[create.index("/TR") + 1]
    assert env.fixup.check()


def test_idempotent(env):
    report = run_fixups([env.fixup], out=lambda line: None)
    assert report.applied == ["node-autoupdate-task"]
    report = run_fixups([env.fixup], out=lambda line: None)
    assert report.already == ["node-autoupdate-task"]


def test_missing_task_is_not_applied(env):
    env.fixup.apply()
    env.root.outputs[QUERY] = None
    assert not env.fixup.check()


def test_changed_script_is_not_applied(env):
    env.fixup.apply()
    env.script.write_text("old", encoding="utf-8")
    assert not env.fixup.check()


def test_quotes_are_escaped(env, monkeypatch):
    monkeypatch.setattr(autoupdate, "pipx_home", lambda: "C:\\o'k")
    env.fixup.apply()
    assert "$PipxHome = 'C:\\o''k'" in env.script.read_bytes().decode("utf-8-sig")


def test_unknown_repo_fails(env, monkeypatch):
    monkeypatch.setattr(autoupdate, "origin_repo_url", lambda: None)
    with pytest.raises(FixupError, match="репозитория"):
        env.fixup.apply()


def test_missing_tool_fails(env):
    env.root.missing_tools.add("pipx")
    with pytest.raises(FixupError, match="pipx"):
        env.fixup.apply()
