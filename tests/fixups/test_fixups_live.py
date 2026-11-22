from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from world import golden_bytes, golden_json

import sa_home_node
from sa_home_node.fixups import node, power, privileged, registry
from sa_home_node.fixups.runner import fix_command

CORE = registry.CORE_FIXUPS
USER = "user-a"
STUB_NAMES = ("sudo", "systemctl", "visudo", "loginctl")
STUB = """import json, os, shutil, sys
from pathlib import Path

name = Path(sys.argv[0]).name
argv = [name, *sys.argv[1:]]
with open(os.environ["STUB_JOURNAL"], "a") as journal:
    journal.write(json.dumps(argv) + "\\n")
if name != "sudo":
    sys.exit(0)
cmd, rest = sys.argv[1], sys.argv[2:]
if cmd == "install":
    flags, paths, i = set(), [], 0
    while i < len(rest):
        if rest[i] in ("-m", "-o", "-g"):
            i += 2
        elif rest[i] in ("-D", "-d"):
            flags.add(rest[i])
            i += 1
        else:
            paths.append(rest[i])
            i += 1
    if "-d" in flags:
        Path(paths[0]).mkdir(parents=True, exist_ok=True)
    else:
        if "-D" in flags:
            Path(paths[-1]).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(paths[-2], paths[-1])
elif cmd == "test":
    sys.exit(0 if Path(rest[1]).exists() else 1)
elif cmd == "loginctl" and rest[0] == "enable-linger":
    target = Path(os.environ["STUB_LINGER"]) / rest[1]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()
else:
    sys.exit(127)
"""


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.root = tmp_path / "root"
        self.bin = tmp_path / "bin"
        self.journal = tmp_path / "journal.jsonl"
        self.unit = self.home / ".config" / "systemd" / "user" / "sa-home-node.service"
        self.rules = self.root / "etc" / "polkit-1" / "rules.d" / "50-sa-home-node-power.rules"
        self.linger = self.root / "var" / "lib" / "systemd" / "linger"
        self.config = tmp_path / "config.toml"

    def write_config(self, extra: str = "") -> Path:
        self.config.write_text(
            f'[node]\nid = "node-a"\nstate_path = "{self.tmp / "state.json"}"\n{extra}\n'
        )
        return self.config

    def write_unit(self) -> None:
        self.unit.parent.mkdir(parents=True, exist_ok=True)
        self.unit.write_bytes(golden_bytes("units/node-before.service"))

    def calls(self) -> list[list[str]]:
        if not self.journal.exists():
            return []
        return [json.loads(line) for line in self.journal.read_text().splitlines()]

    def normalized_calls(self) -> list[list[str]]:
        out = []
        for argv in self.calls():
            argv = [a.replace(str(self.root), "") for a in argv]
            if argv[:2] == ["sudo", "install"] and "-d" not in argv:
                argv[-2] = "<tmp>"
            out.append(argv)
        return out

    def snapshot(self) -> dict[str, bytes | None]:
        return {
            str(p): p.read_bytes() if p.is_file() else None for p in sorted(self.tmp.rglob("*"))
        }

    def run(self, *, only: tuple[str, ...] = (), dry_run: bool = False) -> tuple[int, list[str]]:
        lines: list[str] = []
        code = fix_command(self.config, dry_run=dry_run, only=only, out=lines.append)
        return code, lines


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    e = Env(tmp_path)
    e.bin.mkdir()
    e.home.mkdir()
    for name in STUB_NAMES:
        stub = e.bin / name
        stub.write_text(f"#!{sys.executable}\n{STUB}")
        stub.chmod(0o755)
    (e.bin / "sa-home-node").write_text("")
    (e.bin / "sa-home-node").chmod(0o755)
    monkeypatch.setenv("HOME", str(e.home))
    monkeypatch.setenv("LOGNAME", USER)
    monkeypatch.setenv("PATH", f"{e.bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_JOURNAL", str(e.journal))
    monkeypatch.setenv("STUB_LINGER", str(e.linger))
    monkeypatch.setattr(Path, "home", lambda: e.home)
    monkeypatch.setattr(privileged, "SBIN_FALLBACK_DIRS", ())
    monkeypatch.setattr(node, "LINGER_DIR", e.linger)
    monkeypatch.setattr(power, "POLKIT_RULE_FILE", e.rules)
    monkeypatch.setattr(power, "getuser", lambda: USER)
    monkeypatch.setattr(registry, "CORE_FIXUPS", CORE)
    for name in STUB_NAMES:
        assert shutil.which(name) == str(e.bin / name)
        assert privileged.find_executable(name) == str(e.bin / name)
    return e


def test_unit_command_rewrites_only_exec_line(env) -> None:
    env.write_config()
    env.write_unit()
    before = golden_bytes("units/node-before.service").decode("utf-8")
    expected = before.replace(
        "ExecStart=/home/user-a/.local/bin/sa-home-bot --service node --config",
        f"ExecStart={env.bin}/sa-home-node run --config",
    )
    assert expected != before
    code, lines = env.run(only=("node-unit-command",))
    assert code == 0
    assert any("применено" in line and "уже" not in line for line in lines)
    assert env.unit.read_text() == expected
    assert env.calls() == [["systemctl", "--user", "daemon-reload"]]
    code, lines = env.run(only=("node-unit-command",))
    assert code == 0
    assert any("уже применено" in line for line in lines)
    assert env.unit.read_text() == expected
    assert len(env.calls()) == 1


def test_linger_via_stub(env) -> None:
    env.write_config()
    env.write_unit()
    assert env.run(only=("node-linger",))[0] == 0
    assert env.calls() == [["sudo", "loginctl", "enable-linger", USER]]
    assert (env.linger / USER).exists()
    assert env.run(only=("node-linger",))[0] == 0
    assert len(env.calls()) == 1


def test_polkit_matches_golden(env) -> None:
    env.write_config()
    env.rules.parent.mkdir(parents=True)
    expected = next(c for c in golden_json("argv.json") if c["name"] == "power_polkit_with_rules_dir")
    assert env.run(only=("power-control-polkit",))[0] == 0
    assert env.normalized_calls() == expected["calls"]
    assert env.rules.read_bytes() == golden_bytes("files/power.rules")
    assert env.run(only=("power-control-polkit",))[0] == 0
    assert len(env.calls()) == len(expected["calls"])


def test_dry_run_changes_nothing(env) -> None:
    env.write_config()
    env.write_unit()
    before = env.snapshot()
    code, lines = env.run(dry_run=True)
    assert code == 3
    assert sum("нужно применить" in line for line in lines) == 3
    assert env.snapshot() == before
    assert env.calls() == []


def test_list_without_stubs(env) -> None:
    env.write_config()
    env.write_unit()
    result = subprocess.run(
        [sys.executable, "-m", "sa_home_node", "fix", "--list", "--config", str(env.config)],
        capture_output=True,
        text=True,
        cwd=env.tmp,
        env={**os.environ, "PYTHONPATH": str(Path(sa_home_node.__file__).parents[1])},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    ids = [line.split("\t")[0] for line in result.stdout.splitlines()]
    assert ids == ["node-linger", "node-unit-command", "power-control-polkit"]
    assert env.calls() == []


