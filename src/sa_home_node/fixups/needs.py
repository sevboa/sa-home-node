from __future__ import annotations

from collections.abc import Callable

from sa_home_node.fixups.core import FixupContext

Need = Callable[[FixupContext], bool]


def assigned(*services: str) -> Need:
    return lambda ctx: ctx.assigned(*services)


def power_controllable(ctx: FixupContext) -> bool:
    return ctx.traits.power_controllable


def wakeable(ctx: FixupContext) -> bool:
    return ctx.traits.wakeable


def all_of(*needs: Need) -> Need:
    return lambda ctx: all(need(ctx) for need in needs)
