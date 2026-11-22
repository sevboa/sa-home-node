from __future__ import annotations

import getpass
from pathlib import Path
from types import SimpleNamespace

import pytest
from world import golden_bytes

from sa_home_node.fixups import node
from sa_home_node.fixups.core import FixupError
from sa_home_node.host.base import NodeUnit

CONFIG = "/cfg/config.toml"
SYSTEMD = "[Service]\nExecStart={exec}\nRestart=always\n"
NODE_ARGS = "run --config /home/user-a/sa-home/config.toml"
NODE_BIN = "/home/user-a/.local/bin/sa-home-node"
WIN_NODE_BIN = "C:\\Users\\user-a\\.local\\bin\\sa-home-node.exe"
RELOAD = ["systemctl", "--user", "daemon-reload"]


def make_ctx(tmp_path: Path, fmt: str, host: str = "linux"):
    unit = NodeUnit(fmt, tmp_path / "unit", RELOAD if fmt == "systemd-user" else None)
    fake = SimpleNamespace(name=host, node_unit=lambda path: unit)
    return SimpleNamespace(host=fake, config=SimpleNamespace(path=Path(CONFIG))), unit


def by_id(ctx, fid: str):
    return next(f for f in node.node_fixups(ctx) if f.id == fid)


def test_order_and_platforms(tmp_path) -> None:
    ctx, _ = make_ctx(tmp_path, "systemd-user")
    fixups = node.node_fixups(ctx)
    assert [f.id for f in fixups] == ["node-linger", "node-unit-command"]
    assert [f.platforms for f in fixups] == [("linux",), ("linux", "windows")]


@pytest.mark.parametrize("text", [None, SYSTEMD.format(exec=f"/x/sa-home-node run --config {CONFIG}")])
def test_missing_or_new_form_is_applied(tmp_path, text) -> None:
    ctx, unit = make_ctx(tmp_path, "systemd-user")
    if text is not None:
        unit.path.write_text(text)
    assert by_id(ctx, "node-unit-command").check()


def golden_unit_case(fmt: str):
    if fmt == "systemd-user":
        old = b"ExecStart=/home/user-a/.local/bin/sa-home-bot --service node --config /home/user-a/sa-home/config.toml\n"
        edits = [(old, f"ExecStart={NODE_BIN} {NODE_ARGS}\n".encode())]
        return golden_bytes("units/node-before.service"), edits, NODE_BIN
    exe = b"<executable>C:\\Users\\user-a\\.local\\bin\\sa-home-bot.exe</executable>"
    args = b'<arguments>--service node --config "C:\\sa-home\\config.toml"</arguments>'
    edits = [
        (exe, f"<executable>{WIN_NODE_BIN}</executable>".encode()),
        (args, b"<arguments>run --config C:\\sa-home\\config.toml</arguments>"),
    ]
    return golden_bytes("units/node-before.xml"), edits, WIN_NODE_BIN


@pytest.mark.parametrize(("fmt", "host"), [("systemd-user", "linux"), ("winsw", "windows")])
def test_unit_command_golden(tmp_path, monkeypatch, root, fmt, host) -> None:
    ctx, unit = make_ctx(tmp_path, fmt, host)
    before, edits, binary = golden_unit_case(fmt)
    expected = before
    for old, new in edits:
        assert expected.count(old) == 1
        expected = expected.replace(old, new)
    monkeypatch.setattr(node, "find_executable", lambda name: binary)
    unit.path.write_bytes(before)
    fixup = by_id(ctx, "node-unit-command")
    assert not fixup.check()
    fixup.apply()
    assert unit.path.read_bytes() == expected
    assert fixup.check()
    fixup.apply()
    assert unit.path.read_bytes() == expected
    assert root.calls == ([unit.reload_argv] if unit.reload_argv else [])


@pytest.mark.parametrize(
    "content",
    [SYSTEMD.format(exec="/x/other --flag").encode(), b"\xff\xfe[Service]\n"],
    ids=["unrecognized", "undecodable"],
)
def test_unclear_unit_is_left_alone(tmp_path, content) -> None:
    ctx, unit = make_ctx(tmp_path, "systemd-user")
    unit.path.write_bytes(content)
    with pytest.raises(FixupError, match="правьте руками"):
        by_id(ctx, "node-unit-command").apply()
    assert unit.path.read_bytes() == content


def old_unit(tmp_path: Path, exe: str) -> str:
    return SYSTEMD.format(exec=f"{tmp_path}/{exe} --service node --config {CONFIG}")


def test_no_node_executable(tmp_path, monkeypatch) -> None:
    ctx, unit = make_ctx(tmp_path, "systemd-user")
    unit.path.write_text(old_unit(tmp_path, "other"))
    monkeypatch.setattr(node, "find_executable", lambda name: None)
    with pytest.raises(FixupError, match="sa-home-node"):
        by_id(ctx, "node-unit-command").apply()


def test_write_denied(tmp_path, monkeypatch) -> None:
    ctx, unit = make_ctx(tmp_path, "systemd-user")
    (tmp_path / "sa-home-node").write_text("")
    unit.path.write_text(old_unit(tmp_path, "sa-home-node"))

    def deny(self, data):
        raise PermissionError

    monkeypatch.setattr(Path, "write_bytes", deny)
    with pytest.raises(FixupError, match="права администратора"):
        by_id(ctx, "node-unit-command").apply()


def test_linger(tmp_path, root) -> None:
    ctx, _ = make_ctx(tmp_path, "systemd-user")
    user = getpass.getuser()
    fixup = by_id(ctx, "node-linger")
    assert not fixup.check()
    fixup.apply()
    assert root.sudo_commands("loginctl") == [["loginctl", "enable-linger", user]]
    root.files[f"/var/lib/systemd/linger/{user}"] = ""
    assert fixup.check()


@pytest.mark.parametrize(
    ("fmt", "exists", "expected"),
    [("systemd-user", True, True), ("systemd-user", False, False), ("winsw", True, False)],
)
def test_linger_needed(tmp_path, fmt, exists, expected) -> None:
    ctx, unit = make_ctx(tmp_path, fmt)
    if exists:
        unit.path.write_text("")
    assert by_id(ctx, "node-linger").needed(ctx) is expected
