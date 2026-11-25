import argparse
import shlex

from sa_home_node.backup import bundle
from sa_home_node.backup.cli import add_backup_parser
from sa_home_node.cli import main
from sa_home_node.config import NodeConfig, NodeSection
from sa_home_node.content import ContentStore
from sa_home_node.fixups.registry import CORE_FIXUPS
from sa_home_node.fixups.wol import wol_fixups
from sa_home_node.plugins import Plugin, load_plugins, make_context


def test_builtin_tasks_first():
    assert [p.name for p in load_plugins([])] == ["monitor", "tasks", "metrics", "bot"]


def test_plugin_defaults():
    plugin = Plugin(name="x")
    assert plugin.backup is None
    assert plugin.task_kinds == ()


def test_context_carries_socket_and_token(tmp_path):
    node = NodeSection(id="n", socket=str(tmp_path / "node.sock"))
    config = NodeConfig(path=tmp_path / "config.toml", node=node, sections={"swarm": {"token": "tok"}})
    ctx = make_context(Plugin(name="x"), config, ContentStore(tmp_path / "content"))
    assert ctx.node_socket == str(tmp_path / "node.sock")
    assert ctx.swarm_token == "tok"


def test_wol_fixups_last():
    assert CORE_FIXUPS[-1] is wol_fixups


def test_backup_help_parses(capsys):
    try:
        main(["backup", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "keygen" in capsys.readouterr().out


def test_remote_commands_are_registered(monkeypatch):
    calls = []
    monkeypatch.setattr(bundle.subprocess, "run", lambda argv, **kw: calls.append(argv) or type("R", (), {"returncode": 0})())
    bundle.stage_over_ssh("h", b"", "n")
    bundle.apply_over_ssh("h", "n", "--wipe-db")
    parser = argparse.ArgumentParser()
    add_backup_parser(parser.add_subparsers(dest="command"))
    stage, apply = (parser.parse_args(shlex.split(argv[-1])[1:]) for argv in calls)
    assert stage.backup_command == "stage"
    assert (apply.backup_command, apply.yes, apply.wipe_db) == ("apply", True, True)
