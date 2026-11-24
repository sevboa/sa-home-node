import argparse
import json
import stat
from typing import ClassVar

import pytest

from sa_home_node.backup import cli, sealed
from sa_home_node.plugins import Plugin
from sa_home_node.proto.wire import ProtoError
from tests.backup.test_bk_restore import Keeper, make_spec


class FakeClient:
    peers: ClassVar[list[dict]] = []
    keeper: ClassVar[Keeper | None] = None
    dead: ClassVar[set[str]] = set()
    calls: ClassVar[list] = []

    def __init__(self, endpoint, token=""):
        self.endpoint, self.token = endpoint, token
        self.closed = False

    async def connect(self):
        pass

    async def close(self):
        self.closed = True

    async def get_state(self):
        return {"peers": self.peers}

    async def command(self, action, args, dst=None, timeout=None):
        FakeClient.calls.append((dst.node, dst.service))
        if dst.node in self.dead:
            raise ProtoError("not_found", "нет копии")
        return await self.keeper.ask(action, args)


@pytest.fixture
def keeper():
    return Keeper(make_spec())


@pytest.fixture
def env(tmp_path, monkeypatch, keeper):
    key = tmp_path / "backup.key"
    key.write_text(sealed.dump_key(keeper.priv) + "\n", encoding="ascii")
    config = tmp_path / "config.toml"
    write_config(config, f'[backup]\nprivate_key_file = "{key}"\n')
    FakeClient.peers = [
        {"id": "node-a", "alive": True},
        {"id": "node-c", "alive": False},
        {"id": "node-b", "alive": True},
    ]
    FakeClient.keeper, FakeClient.dead, FakeClient.calls = keeper, set(), []
    monkeypatch.setattr(cli, "ProtoClient", FakeClient)
    monkeypatch.setattr(cli, "load_plugins", lambda names: [Plugin("x"), Plugin("p", backup=keeper.spec)])
    return config


def write_config(path, extra=""):
    path.write_text(f'[node]\nid = "node-b"\nplugins = ["p"]\n{extra}', encoding="utf-8")


def invoke(argv):
    parser = argparse.ArgumentParser()
    cli.add_backup_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["backup", *argv])
    return args._run(args)


def test_keygen(tmp_path, capsys):
    out = tmp_path / "d" / "k"
    assert invoke(["keygen", "--out", str(out)]) == 0
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    text = capsys.readouterr().out
    public = next(ln for ln in text.splitlines() if "recipient_public_key" in ln).split('"')[1]
    assert len(public) == 44
    assert sealed.load_key(public) == sealed.public_from_private(sealed.read_private_key(out))
    assert sealed.dump_key(sealed.read_private_key(out)) not in text
    assert invoke(["keygen", "--out", str(out)]) == 1
    assert "не затираю" in capsys.readouterr().err


def test_list_finds_first_live_holder(env, capsys):
    assert invoke(["--config", str(env), "list", "node-a"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Копии ноды node-a у node-b:\nidentity:\n  latest")
    assert "снапшот БД:\n  latest" in out
    assert FakeClient.calls[0] == ("node-b", "svc")


def test_list_explicit_holder_and_empty(env, keeper, capsys):
    keeper.identity, keeper.snapshots = False, ()
    assert invoke(["--config", str(env), "list", "node-a", "--holder", "node-z"]) == 0
    assert capsys.readouterr().out == "Копии ноды node-a у node-z:\nidentity:\n  —\nснапшот БД:\n  —\n"


def test_list_no_holder_found(env, capsys):
    FakeClient.dead = {"node-b"}
    assert invoke(["--config", str(env), "list", "node-a"]) == 1
    assert "не нашёл живого хранителя копии node-a среди пиров ['node-b']" in capsys.readouterr().err


def test_restore_dry_run(env, tmp_path, capsys):
    target = tmp_path / "out"
    assert invoke(["--config", str(env), "restore", "node-a", "--dry-run", str(target)]) == 0
    out = capsys.readouterr().out
    assert "Нода: node-a" in out and "Ноды не тронуты" in out
    assert sorted(p.name for p in target.iterdir()) == ["identity.json", "meta.json", "snapshot.json", "summary.txt"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert json.loads((target / "identity.json").read_text()) == {"secret": "s"}
    assert invoke(["--config", str(env), "restore", "node-a", "--dry-run", str(target)]) == 1
    assert "не пуст" in capsys.readouterr().err


def test_restore_no_snapshot(env, tmp_path, capsys):
    target = tmp_path / "out"
    assert invoke(["--config", str(env), "restore", "node-a", "--no-snapshot", "--dry-run", str(target)]) == 0
    assert not (target / "snapshot.json").exists()


def test_restore_apply_over_ssh(env, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli.bundles, "stage_over_ssh", lambda host, payload, remote: calls.append(("stage", host, remote)))
    monkeypatch.setattr(cli.bundles, "apply_over_ssh", lambda host, remote, extra: calls.append(("apply", host, extra)) or 3)
    argv = ["--config", str(env), "restore", "node-a", "--apply", "--yes", "--wipe-db", "--ssh-host", "h", "--remote-nodectl", "nc"]
    assert invoke(argv) == 3
    assert calls == [("stage", "h", "nc"), ("apply", "h", "--wipe-db")]
    assert "backup apply на h завершился кодом 3" in capsys.readouterr().err


def test_restore_apply_declined(env, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert invoke(["--config", str(env), "restore", "node-a", "--apply"]) == 1
    assert "Отменено." in capsys.readouterr().out


def test_restore_without_private_key(env, tmp_path, capsys):
    write_config(env)
    assert invoke(["--config", str(env), "restore", "node-a", "--dry-run", str(tmp_path / "o")]) == 1
    assert "[backup].private_key_file не задан" in capsys.readouterr().err


def test_restore_wrong_key(env, tmp_path, capsys):
    other = tmp_path / "other.key"
    other.write_text(sealed.dump_key(sealed.generate_keypair()[0]) + "\n", encoding="ascii")
    write_config(env, f'[backup]\nprivate_key_file = "{other}"\n')
    assert invoke(["--config", str(env), "restore", "node-a", "--dry-run", str(tmp_path / "o")]) == 1
    assert "Ошибка:" in capsys.readouterr().err


def test_no_backup_plugin(env, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_plugins", lambda names: [Plugin("x")])
    assert invoke(["--config", str(env), "list", "node-a"]) == 1
    assert "Ошибка: нет плагина с бэкапом" in capsys.readouterr().err


def test_connection_error(env, monkeypatch, capsys):
    async def boom(self):
        raise ConnectionRefusedError("отказ")

    monkeypatch.setattr(FakeClient, "connect", boom)
    assert invoke(["--config", str(env), "list", "node-a"]) == 1
    assert "Нет связи с нодой/хранителем: отказ" in capsys.readouterr().err


def test_missing_config(tmp_path, capsys):
    assert invoke(["--config", str(tmp_path / "no.toml"), "list", "node-a"]) == 2
    assert "ошибка:" in capsys.readouterr().err


def test_local_commands_delegate(env, monkeypatch):
    seen = []
    monkeypatch.setattr(cli.local, "run_stage", lambda config: seen.append("stage") or 0)
    monkeypatch.setattr(cli.local, "run_release", lambda config: seen.append("release") or 0)
    monkeypatch.setattr(
        cli.local, "run_apply", lambda config, specs, **kw: seen.append((len(specs), kw)) or 0
    )
    for name in ("stage", "release"):
        assert invoke(["--config", str(env), name]) == 0
    assert invoke(["--config", str(env), "apply", "--yes", "--bundle", "b"]) == 0
    assert seen == ["stage", "release", (1, {"bundle": "b", "yes": True, "wipe_db": False})]
