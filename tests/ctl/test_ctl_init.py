from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from sa_home_node.config import load_config
from sa_home_node.host.base import NodeUnit
from sa_home_node.wizard.run import add_init_parser, run_init

RELOAD = ["systemctl", "--user", "daemon-reload"]
ENABLE = ["systemctl", "--user", "enable", "--now", "sa-home-node.service"]
LINGER = ["loginctl", "enable-linger", "user-a"]


def parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_init_parser(parser.add_subparsers(dest="command"))
    return parser.parse_args(["init", *argv])


class Rig:
    def __init__(self, tmp_path: Path, unit_format: str = "systemd-user") -> None:
        self.unit_path = tmp_path / "units" / "sa-home-node.service"
        self.config = tmp_path / "cfg" / "config.toml"
        self.workdir = tmp_path / "work"
        self.steps: list[list[str]] = []
        self.fixed: list[Path] = []
        unit = NodeUnit(unit_format, self.unit_path, RELOAD, ENABLE, LINGER)
        self.host = SimpleNamespace(node_unit=lambda path: unit, elevate_argv=lambda argv: ["sudo", *argv])

    def run(self, *argv: str, interactive: bool = False, answers: tuple[str, ...] = ()) -> int:
        feed = iter(answers)

        def reply(question: str) -> str:
            try:
                return next(feed)
            except StopIteration:
                raise EOFError from None

        def run_step(argv: list[str]) -> bool:
            self.steps.append(argv)
            return True

        def fix(path: Path) -> int:
            self.fixed.append(path)
            return 0

        args = parse("--config", str(self.config), "--data-dir", str(self.workdir), *argv)
        return run_init(
            args,
            reply=reply,
            interactive=interactive,
            host=self.host,
            run_step=run_step,
            fix=fix,
            executable="/opt/bin/sa-home-node",
        )


FLAGS = ("--node-id", "n1", "--kind", "server", "--assignments", "monitor", "--token", "tok")


def test_non_interactive_writes_everything(tmp_path, capsys):
    rig = Rig(tmp_path)
    assert rig.run(*FLAGS) == 0
    cfg = load_config(rig.config)
    assert (cfg.node.id, cfg.node.kind, cfg.node.assignments) == ("n1", "server", ["monitor"])
    assert cfg.node.listen == ["tcp://0.0.0.0:8710"]
    assert (rig.workdir / "data").is_dir()
    unit = rig.unit_path.read_text(encoding="utf-8")
    assert f"ExecStart=/opt/bin/sa-home-node run --config {rig.config}" in unit
    assert rig.steps == [RELOAD, ENABLE]
    assert rig.fixed == []
    out = capsys.readouterr().out
    assert "tok" in out
    assert "sudo loginctl enable-linger user-a" in out
    assert "sa-home-node fix" in out


def test_interactive_runs_fixups_and_linger(tmp_path):
    rig = Rig(tmp_path)
    assert rig.run(*FLAGS, "--listen", "", "--join", "", interactive=True) == 0
    assert rig.fixed == [rig.config]
    assert rig.steps == [RELOAD, ENABLE, ["sudo", *LINGER]]


def test_join_without_token_fails(tmp_path, capsys):
    rig = Rig(tmp_path)
    assert rig.run("--node-id", "n1", "--join", "tcp://h:8710") == 2
    assert not rig.config.exists()
    assert "--token" in capsys.readouterr().err


def test_existing_config_kept(tmp_path, capsys):
    rig = Rig(tmp_path)
    rig.config.parent.mkdir(parents=True)
    rig.config.write_text("old", encoding="utf-8")
    assert rig.run(*FLAGS) == 1
    assert rig.config.read_text(encoding="utf-8") == "old"
    assert "--force" in capsys.readouterr().err
    assert rig.run(*FLAGS, "--force") == 0
    assert load_config(rig.config).node.id == "n1"


def test_existing_unit_kept_without_force(tmp_path):
    rig = Rig(tmp_path)
    rig.unit_path.parent.mkdir(parents=True)
    rig.unit_path.write_text("old unit", encoding="utf-8")
    assert rig.run(*FLAGS) == 0
    assert rig.unit_path.read_text(encoding="utf-8") == "old unit"
    assert rig.steps == []


def test_no_start(tmp_path, capsys):
    rig = Rig(tmp_path)
    assert rig.run(*FLAGS, "--no-start") == 0
    assert rig.unit_path.exists()
    assert rig.steps == []
    assert "systemctl --user enable --now sa-home-node.service" in capsys.readouterr().out


@pytest.mark.parametrize(("unit_format", "flag"), [("winsw", ()), ("systemd-user", ("--no-unit",))])
def test_no_unit(tmp_path, capsys, unit_format, flag):
    rig = Rig(tmp_path, unit_format)
    assert rig.run(*FLAGS, *flag) == 0
    assert not rig.unit_path.exists()
    assert rig.steps == []
    assert f"sa-home-node run --config {rig.config}" in capsys.readouterr().out


def test_generated_token_when_founding(tmp_path, capsys):
    rig = Rig(tmp_path)
    assert rig.run("--node-id", "n1", "--kind", "vps", "--listen", "tcp://10.0.0.1:8710") == 0
    token = load_config(rig.config).sections["swarm"]["token"]
    assert len(token) >= 32
    assert token in capsys.readouterr().out
