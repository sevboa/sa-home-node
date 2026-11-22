from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from sa_home_node.config import NodeConfig, T
from sa_home_node.services.assignments import Assignment, effective, parse_valid
from sa_home_node.swarm.kind import NodeTraits, traits_for
from sa_home_node.swarm.state import NodeState

if TYPE_CHECKING:
    from sa_home_node.host.base import Host


class FixupError(Exception):
    pass


@dataclass(frozen=True)
class FixupContext:
    config: NodeConfig
    assignments: tuple[Assignment, ...]
    traits: NodeTraits
    host: Host

    @classmethod
    def build(cls, config: NodeConfig, host: Host) -> FixupContext:
        state = NodeState.load(config.state_file)
        assignments = tuple(parse_valid(effective(config.node.assignments, state.assignments)))
        traits = traits_for(config.node.kind)
        if config.node.power_controllable is not None:
            traits = replace(traits, always_on=not config.node.power_controllable)
        return cls(config, assignments, traits, host)

    def assigned(self, *services: str) -> bool:
        return any(a.service in services for a in self.assignments)

    def section(self, name: str, model: type[T]) -> T:
        return self.config.section(name, model)


@dataclass(frozen=True)
class Fixup:
    id: str
    title: str
    check: Callable[[], bool]
    apply: Callable[[], None]
    needed: Callable[[FixupContext], bool] = lambda ctx: True
    platforms: tuple[str, ...] = ("linux",)


@dataclass
class FixupReport:
    applied: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _check_or_false(fixup: Fixup, out: Callable[[str], None], *, dry_run: bool = False) -> bool:
    try:
        return fixup.check()
    except (FixupError, OSError) as exc:
        out(f"  ⚠️ {fixup.title}: проверка не удалась ({exc}){'' if dry_run else ' — пробую применить'}")
        return False


def run_fixups(
    fixups: list[Fixup], *, dry_run: bool = False, out: Callable[[str], None] = print
) -> FixupReport:
    report = FixupReport()
    for fixup in fixups:
        if _check_or_false(fixup, out, dry_run=dry_run):
            out(f"  ✅ {fixup.title} — уже применено")
            report.already.append(fixup.id)
            continue
        if dry_run:
            out(f"  🔍 {fixup.title} — нужно применить")
            report.pending.append(fixup.id)
            continue
        out(f"  ⏳ {fixup.title} — применяю (может спросить пароль sudo)...")
        try:
            fixup.apply()
        except (FixupError, OSError) as exc:
            out(f"  ❌ {fixup.title}: {exc}")
            report.failed.append(fixup.id)
            continue
        if _check_or_false(fixup, out):
            out(f"  ✅ {fixup.title} — применено")
            report.applied.append(fixup.id)
            continue
        out(f"  ⚠️ {fixup.title}: команда прошла, но проверка всё ещё отрицательна")
        report.failed.append(fixup.id)
    return report
