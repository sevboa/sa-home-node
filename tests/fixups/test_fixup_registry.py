from __future__ import annotations

from types import SimpleNamespace

import pytest
from world import golden_json, make_config

from sa_home_node.fixups import registry
from sa_home_node.fixups.core import Fixup, FixupContext
from sa_home_node.fixups.registry import collect, select
from sa_home_node.host import select as select_host
from sa_home_node.plugins import Plugin, PluginError


def fx(fid: str, **kw) -> Fixup:
    return Fixup(fid, f"T-{fid}", lambda: True, lambda: None, **kw)


def ctx(host: str = "linux", services: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(
        host=SimpleNamespace(name=host),
        assigned=lambda *s: any(x in services for x in s),
    )


def plugin(name: str, *fixups: Fixup) -> Plugin:
    return Plugin(name, fixups=lambda c: list(fixups))


def ids(fixups: list[Fixup]) -> list[str]:
    return [f.id for f in fixups]


def test_order_core_then_plugins(monkeypatch) -> None:
    monkeypatch.setattr(registry, "CORE_FIXUPS", (lambda c: [fx("c1"), fx("c2")], lambda c: [fx("c3")]))
    plugins = [plugin("p2", fx("b1")), Plugin("none"), plugin("p1", fx("a1"), fx("a2"))]
    assert ids(collect(ctx(), plugins)) == ["c1", "c2", "c3", "b1", "a1", "a2"]


def test_filters_and_factory_context() -> None:
    both = fx("both", platforms=("linux", "windows"))
    win = fx("win", platforms=("windows",))
    svc = fx("svc", needed=lambda c: c.assigned("svc"))
    plugins = [plugin("p", both, win, svc), Plugin("q", fixups=lambda c: [fx("q")] if c.assigned("svc") else [])]
    assert ids(collect(ctx("linux"), plugins)) == ["both"]
    assert ids(collect(ctx("windows"), plugins)) == ["both", "win"]
    assert ids(collect(ctx("linux", ("svc",)), plugins)) == ["both", "svc", "q"]


@pytest.mark.parametrize(("in_core", "owners"), [(True, "core, p"), (False, "p, q")])
def test_duplicate_id_fails_even_when_filtered_out(monkeypatch, in_core, owners) -> None:
    hidden = plugin("p", fx("x", platforms=("windows",)))
    if in_core:
        monkeypatch.setattr(registry, "CORE_FIXUPS", (lambda c: [fx("x")],))
        plugins = [hidden]
    else:
        plugins = [hidden, plugin("q", fx("x"))]
    with pytest.raises(PluginError, match=rf"повтор id фиксапа: x \({owners}\)"):
        collect(ctx(), plugins)


def test_factory_failure_reports_all_plugins() -> None:
    def boom(c):
        raise ValueError("сломано")

    plugins = [Plugin("bad1", fixups=boom), plugin("ok", fx("x")), Plugin("bad2", fixups=boom)]
    with pytest.raises(PluginError) as info:
        collect(ctx(), plugins)
    text = str(info.value)
    assert "bad1" in text and "bad2" in text and "сломано" in text
    assert "ok" not in text


def test_select() -> None:
    fixups = [fx("a"), fx("b"), fx("c")]
    assert ids(select(fixups, ["c", "a"])) == ["a", "c"]
    assert select(fixups, []) == []
    with pytest.raises(PluginError, match=r"Неизвестные фиксапы: z; доступны: a, b, c"):
        select(fixups, ["a", "z"])


CATALOG = golden_json("catalog.json")
CORE = registry.CORE_FIXUPS
POWER = "power-control-polkit"


def core_context(tmp_path, kind: str) -> FixupContext:
    return FixupContext.build(make_config(tmp_path, f'kind = "{kind}"'), select_host("linux"))


def test_catalog_core_new_ids_and_order(tmp_path) -> None:
    assert [f.id for f in CORE[0](core_context(tmp_path, "server"))] == CATALOG["core_new"]
    all_ids = [f.id for factory in CORE for f in factory(core_context(tmp_path, "workstation"))]
    positions = [all_ids.index(i) for i in [*CATALOG["core_new"], POWER]]
    assert positions == sorted(positions)


@pytest.mark.parametrize("kind", ("server", "workstation", "vps"))
def test_catalog_power_fixup(tmp_path, kind) -> None:
    ctx = core_context(tmp_path, kind)
    fixups = [f for factory in CORE for f in factory(ctx) if f.id == POWER]
    assert len(fixups) == 1
    for name, items in CATALOG["cases"].items():
        if name.startswith(f"{kind}/"):
            listed = [i for i in items if i["id"] == POWER]
            assert bool(listed) is fixups[0].needed(ctx)
            assert all(i["title"] == fixups[0].title for i in listed)
