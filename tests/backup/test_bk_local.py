from __future__ import annotations

import io
import json
import stat

import pytest

from sa_home_node.backup import local
from sa_home_node.backup.hold import marker_path
from sa_home_node.backup.spec import BackupSpec
from sa_home_node.config import NodeConfig, NodeSection
from sa_home_node.plugins import Plugin

BUNDLE = {
    "format": "svc/restore/1",
    "node": "node-a",
    "identity_label": "latest",
    "identity": {"format": "svc/identity/1"},
    "snapshot": None,
}


async def noop(*_):
    return {}


def make_plugin(calls=None, spec=None):
    return Plugin(name="p", backup=spec or make_spec([] if calls is None else calls))


def make_spec(calls):
    async def apply(ctx):
        calls.append((ctx.config_path, ctx.wipe_db))

    return BackupSpec(
        service="svc",
        identity_service="svc-identity",
        identity_format="svc/identity/1",
        snapshot_format="svc/snapshot/1",
        bundle_format="svc/restore/1",
        tables=(),
        skip_tables=frozenset(),
        primary_table="t",
        trigger_actions=frozenset(),
        collect_identity=noop,
        apply=apply,
        summarize=lambda bundle: [],
        rows_extra=lambda tables: {},
    )


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[node]\nid = "node-a"\n', encoding="utf-8")
    node = NodeSection(id="node-a", state_path=str(tmp_path / "state" / "node-state.json"))
    return NodeConfig(path=path, node=node, sections={})


def staged(config, bundle=BUNDLE):
    path = local.staged_bundle_path(config.state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return path


def test_staged_path(config):
    assert local.staged_bundle_path(config.state_file) == config.state_file.parent / "restore-bundle.json"


def test_stage_writes_private(config, capsys):
    assert local.run_stage(config, io.BytesIO(b'{"a": 1}')) == 0
    path = local.staged_bundle_path(config.state_file)
    assert path.read_bytes() == b'{"a": 1}'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert capsys.readouterr().out == f"Бандл принят: {path}\n"


@pytest.mark.parametrize("data", [b"", b" \n\t"])
def test_stage_empty_refused(config, capsys, data):
    assert local.run_stage(config, io.BytesIO(data)) == 1
    assert not local.staged_bundle_path(config.state_file).exists()
    assert "Пустой ввод: бандл не получен." in capsys.readouterr().err


@pytest.mark.parametrize("answer", ["", "n", "нет"])
def test_apply_declined(config, capsys, answer):
    calls = []
    path = staged(config)
    code = local.run_apply(config, [make_plugin(calls)], bundle=None, yes=False, wipe_db=False, ask=lambda _: answer)
    assert code == 1 and not calls and path.exists()
    assert "Отменено." in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["y", "YES", " д "])
def test_apply_accepted_removes_staged(config, capsys, answer):
    calls = []
    path = staged(config)
    asked = []
    code = local.run_apply(
        config, [make_plugin(calls)], bundle=None, yes=False, wipe_db=True,
        ask=lambda q: asked.append(q) or answer,
    )
    assert code == 0 and not path.exists()
    assert asked == ["Применить на этой ноде? [y/N] "]
    assert calls == [(config.path, True)]
    assert capsys.readouterr().out.rstrip().endswith("Готово. Дальше: nodectl fix и запуск ноды.")
    assert marker_path(config.state_file).exists()


def test_apply_explicit_bundle_kept(config, tmp_path):
    calls = []
    other = tmp_path / "other.json"
    other.write_text(json.dumps(BUNDLE), encoding="utf-8")
    staged(config)
    assert local.run_apply(config, [make_plugin(calls)], bundle=other, yes=True, wipe_db=False) == 0
    assert other.exists() and local.staged_bundle_path(config.state_file).exists()


@pytest.mark.parametrize(
    ("bundle", "message"),
    [
        ({**BUNDLE, "node": "node-c"}, "Ошибка: бандл для ноды 'node-c'"),
        ({**BUNDLE, "format": "x"}, "Ошибка: неизвестный формат бандла: 'x'"),
    ],
)
def test_apply_known_errors(config, capsys, bundle, message):
    path = staged(config, bundle)
    assert local.run_apply(config, [make_plugin()], bundle=None, yes=True, wipe_db=False) == 1
    assert message in capsys.readouterr().err
    assert path.exists()


def test_apply_unexpected_error(config, capsys):
    async def boom(ctx):
        raise OSError("диск")

    spec = make_spec([])
    spec = BackupSpec(**{**spec.__dict__, "apply": boom})
    staged(config)
    assert local.run_apply(config, [make_plugin(spec=spec)], bundle=None, yes=True, wipe_db=False) == 1
    assert "Не удалось применить: диск" in capsys.readouterr().err


def test_release(config, capsys):
    assert local.run_release(config) == 0
    path = marker_path(config.state_file)
    assert path.exists()
    assert capsys.readouterr().out == f"Публикация бэкапа разрешена: {path}\n"
