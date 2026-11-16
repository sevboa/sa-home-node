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


def test_no_command(capsys):
    assert cli.main([]) == 2
    assert capsys.readouterr().out
