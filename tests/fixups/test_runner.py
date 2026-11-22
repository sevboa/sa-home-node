from __future__ import annotations

from pathlib import Path

import pytest

from sa_home_node.fixups import runner
from sa_home_node.fixups.core import Fixup, FixupError
from sa_home_node.fixups.runner import fix_command
from sa_home_node.plugins import Plugin


def write_config(tmp_path: Path, assignments: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        f'[node]\nid = "node-a"\nstate_path = "{tmp_path / "state.json"}"\n'
        f'plugins = ["pl"]\nassignments = [{assignments}]\n'
    )
    return path


class Probe:
    def __init__(self, state: dict[str, bool]) -> None:
        self.state = state
        self.fail: set[str] = set()
        self.checks: list[str] = []
        self.applies: list[str] = []

    def fixup(self, fid: str) -> Fixup:
        def check() -> bool:
            self.checks.append(fid)
            return self.state[fid]

        def apply() -> None:
            self.applies.append(fid)
            if fid in self.fail:
                raise FixupError("нет прав")
            self.state[fid] = True

        return Fixup(fid, f"T-{fid}", check, apply)


@pytest.fixture
def probe(monkeypatch) -> Probe:
    p = Probe({"a": False, "b": True})
    plugin = Plugin("pl", fixups=lambda ctx: [p.fixup("a"), p.fixup("b")])
    monkeypatch.setattr(runner, "load_plugins", lambda names: [plugin])
    return p


def run(tmp_path: Path, assignments: str = "", **kw) -> tuple[int, list[str]]:
    lines: list[str] = []
    return fix_command(write_config(tmp_path, assignments), out=lines.append, **kw), lines


def test_apply_lines(tmp_path, probe) -> None:
    code, lines = run(tmp_path, '"svc"')
    assert code == 0
    assert lines == [
        "Нужно проверить 2 фикс(ов) под назначения ['svc']:",
        "  ⏳ T-a — применяю (может спросить пароль sudo)...",
        "  ✅ T-a — применено",
        "  ✅ T-b — уже применено",
        "\nГотово. Перезапустите затронутые службы: nodectl restart <name>.",
    ]
    assert probe.applies == ["a"]


def test_list_does_not_check(tmp_path, probe) -> None:
    assert run(tmp_path, list_only=True) == (0, ["a\tT-a", "b\tT-b"])
    assert probe.checks == []


@pytest.mark.parametrize(("state_a", "code"), [(False, 3), (True, 0)])
def test_dry_run(tmp_path, probe, state_a, code) -> None:
    probe.state["a"] = state_a
    assert run(tmp_path, dry_run=True)[0] == code
    assert probe.applies == []


def test_failure(tmp_path, probe) -> None:
    probe.fail = {"a"}
    code, lines = run(tmp_path)
    assert code == 1
    assert "  ❌ T-a: нет прав" in lines
    assert "\nНе применилось: a — см. вывод выше." in lines


def test_only(tmp_path, probe) -> None:
    code, lines = run(tmp_path, only=("b",))
    assert code == 0
    assert probe.checks == ["b"]
    assert lines[0].startswith("Нужно проверить 1 фикс(ов)")


def test_only_unknown(tmp_path, probe, capsys) -> None:
    assert run(tmp_path, only=("zz",)) == (2, [])
    assert "ошибка: Неизвестные фиксапы: zz; доступны: a, b" in capsys.readouterr().err


def test_nothing_needed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runner, "load_plugins", lambda names: [Plugin("pl")])
    expected = "Все известные фиксы либо не нужны для текущих назначений, либо уже применены."
    assert run(tmp_path) == (0, [expected])


def test_config_error(tmp_path, capsys) -> None:
    assert fix_command(tmp_path / "nope.toml", out=lambda s: None) == 2
    assert "ошибка:" in capsys.readouterr().err


def test_plugin_error(tmp_path, capsys) -> None:
    assert run(tmp_path)[0] == 2
    assert "ошибка:" in capsys.readouterr().err
