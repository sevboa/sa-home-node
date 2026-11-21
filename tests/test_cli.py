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


def test_no_command(capsys):
    assert cli.main([]) == 2
    assert capsys.readouterr().out