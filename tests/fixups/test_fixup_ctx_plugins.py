from __future__ import annotations

from pathlib import Path

from world import make_config

from sa_home_node.fixups import runner
from sa_home_node.fixups.core import FixupContext
from sa_home_node.host import select
from sa_home_node.plugins import Plugin


def test_build_without_plugins(tmp_path: Path) -> None:
    assert FixupContext.build(make_config(tmp_path), select("linux")).plugins == ()


def test_build_keeps_plugins(tmp_path: Path) -> None:
    plugins = [Plugin("p"), Plugin("q")]
    ctx = FixupContext.build(make_config(tmp_path), select("linux"), plugins)
    assert ctx.plugins == tuple(plugins)


def test_fix_command_gives_plugins_to_factories(tmp_path: Path, monkeypatch) -> None:
    seen: list[list[str]] = []

    def factory(ctx: FixupContext) -> list:
        seen.append([p.name for p in ctx.plugins])
        return []

    plugins = [Plugin("p", fixups=factory), Plugin("q"), Plugin("r", fixups=factory)]
    monkeypatch.setattr(runner, "load_config", lambda path: make_config(tmp_path))
    monkeypatch.setattr(runner, "load_plugins", lambda names: plugins)
    assert runner.fix_command(None, list_only=True, out=lambda line: None) == 0
    assert seen == [["p", "q", "r"], ["p", "q", "r"]]
