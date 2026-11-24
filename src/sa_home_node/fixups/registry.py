from __future__ import annotations

from collections.abc import Callable, Sequence

from sa_home_node.fixups.autoupdate import autoupdate_fixups
from sa_home_node.fixups.core import Fixup, FixupContext
from sa_home_node.fixups.monitor import monitor_fixups
from sa_home_node.fixups.node import node_fixups
from sa_home_node.fixups.power import power_fixups
from sa_home_node.fixups.wol import wol_fixups
from sa_home_node.plugins import Plugin, PluginError

Factory = Callable[[FixupContext], Sequence[Fixup]]

CORE_FIXUPS: tuple[Factory, ...] = (node_fixups, autoupdate_fixups, monitor_fixups, power_fixups, wol_fixups)

CORE_OWNER = "core"


def _build(ctx: FixupContext, owner: str, factory: Factory, errors: list[str]) -> list[tuple[str, Fixup]]:
    try:
        return [(owner, fixup) for fixup in factory(ctx)]
    except Exception as exc:  # noqa: BLE001
        errors.append(f"ошибка фабрики фиксапов {owner}: {exc}")
        return []


def collect(ctx: FixupContext, plugins: Sequence[Plugin]) -> list[Fixup]:
    errors: list[str] = []
    built: list[tuple[str, Fixup]] = []
    for factory in CORE_FIXUPS:
        built += _build(ctx, CORE_OWNER, factory, errors)
    for plugin in plugins:
        if plugin.fixups is not None:
            built += _build(ctx, plugin.name, plugin.fixups, errors)
    owners: dict[str, list[str]] = {}
    for owner, fixup in built:
        owners.setdefault(fixup.id, []).append(owner)
    errors += [f"повтор id фиксапа: {fid} ({', '.join(who)})" for fid, who in owners.items() if len(who) > 1]
    if errors:
        raise PluginError("; ".join(errors))
    return [fixup for _, fixup in built if ctx.host.name in fixup.platforms and fixup.needed(ctx)]


def select(fixups: list[Fixup], only: Sequence[str]) -> list[Fixup]:
    known = {fixup.id for fixup in fixups}
    unknown = [fid for fid in only if fid not in known]
    if unknown:
        available = ", ".join(fixup.id for fixup in fixups) or "нет"
        raise PluginError(f"Неизвестные фиксапы: {', '.join(unknown)}; доступны: {available}")
    return [fixup for fixup in fixups if fixup.id in only]
