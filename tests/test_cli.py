import pytest

from sa_home_node import cli


def test_plugins_list(capsys, monkeypatch):
    monkeypatch.setattr(cli, "available", lambda: {"b": None, "a": None})
    assert cli.main(["plugins"]) == 0
    assert capsys.readouterr().out == "a\nb\n"


def test_plugins_empty(capsys, monkeypatch):
    monkeypatch.setattr(cli, "available", dict)
    assert cli.main(["plugins"]) == 0
    assert capsys.readouterr().out == "плагины не установлены\n"


def test_run_missing_config(capsys, tmp_path):
    assert cli.main(["run", "--config", str(tmp_path / "nope.toml")]) == 2
    assert "ошибка:" in capsys.readouterr().err


def test_run_passes_config(monkeypatch, tmp_path):
    called = []

    async def run_node(config_path):
        called.append(config_path)

    monkeypatch.setattr(cli, "run_node", run_node)
    assert cli.main(["run", "--config", str(tmp_path / "cfg.toml")]) == 0
    assert called == [tmp_path / "cfg.toml"]


@pytest.mark.parametrize("code", [0, 11])
def test_service_passes_config(monkeypatch, tmp_path, code):
    called = []

    async def run_service(name, instance, config_path):
        called.append((name, instance, config_path))
        return code

    monkeypatch.setattr(cli, "run_service", run_service)
    assert cli.main(["service", "svc", "--instance", "one", "--config", str(tmp_path / "cfg.toml")]) == code
    assert called == [("svc", "one", tmp_path / "cfg.toml")]


def test_service_missing_config(capsys, tmp_path):
    assert cli.main(["service", "svc", "--config", str(tmp_path / "nope.toml")]) == 2
    assert "ошибка:" in capsys.readouterr().err


def test_run_self_restarts(monkeypatch, tmp_path):
    restarts = []

    async def run_node(config_path):
        return True

    class FakeHost:
        def self_restart(self, argv):
            restarts.append(argv)
            return 10

    monkeypatch.setattr(cli, "run_node", run_node)
    monkeypatch.setattr(cli, "current", FakeHost)
    monkeypatch.setattr(cli.sys, "argv", ["sa-home-node", "run"])
    assert cli.main(["run", "--config", str(tmp_path / "cfg.toml")]) == 10
    assert restarts == [["sa-home-node", "run"]]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["fix"], (None, False, (), False)),
        (["fix", "--list", "--dry-run", "--only", "a,b"], (None, True, ("a", "b"), True)),
    ],
)
def test_fix_passes_args(monkeypatch, argv, expected):
    called = []

    def fix_command(config_path, *, dry_run, only, list_only):
        called.append((config_path, dry_run, only, list_only))
        return 3

    monkeypatch.setattr(cli, "fix_command", fix_command)
    assert cli.main(argv) == 3
    assert called == [expected]


def test_fix_missing_config(capsys, tmp_path):
    assert cli.main(["fix", "--config", str(tmp_path / "nope.toml")]) == 2
    assert "ошибка:" in capsys.readouterr().err


def test_run_node_ignores_fixups(tmp_path, monkeypatch):
    from sa_home_node import node
    from sa_home_node.fixups.core import Fixup
    from sa_home_node.plugins import Plugin

    def trap():
        raise AssertionError("fixup called")

    plugin = Plugin("pl", fixups=lambda ctx: [Fixup("t", "T", trap, trap)])
    monkeypatch.setattr(node, "load_plugins", lambda names: [plugin])
    path = tmp_path / "config.toml"
    path.write_text(f'[node]\nid = "node-a"\ndata_dir = "{tmp_path}"\nplugins = ["pl"]\nsocket = "{tmp_path}/s.sock"\n')
    import asyncio

    stop = asyncio.Event()
    stop.set()
    asyncio.run(node.run_node(path, stop))


def test_no_command(capsys):
    assert cli.main([]) == 2
    assert capsys.readouterr().out