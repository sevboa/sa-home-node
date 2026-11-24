from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from sa_home_node.config import ConfigError, load_config
from sa_home_node.fixups.core import FixupContext, run_fixups
from sa_home_node.fixups.registry import collect, select
from sa_home_node.host import current
from sa_home_node.plugins import PluginError, load_plugins


def fix_command(
    config_path: Path | None,
    *,
    dry_run: bool = False,
    only: tuple[str, ...] = (),
    list_only: bool = False,
    out: Callable[[str], None] = print,
) -> int:
    try:
        config = load_config(config_path)
        plugins = load_plugins(config.node.plugins)
        ctx = FixupContext.build(config, current(), plugins)
        fixups = collect(ctx, plugins)
        if only:
            fixups = select(fixups, only)
    except (ConfigError, PluginError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    if list_only:
        for fixup in fixups:
            out(f"{fixup.id}\t{fixup.title}")
        return 0
    if not fixups:
        out("Все известные фиксы либо не нужны для текущих назначений, либо уже применены.")
        return 0
    out(f"Нужно проверить {len(fixups)} фикс(ов) под назначения {[a.key for a in ctx.assignments]}:")
    report = run_fixups(fixups, dry_run=dry_run, out=out)
    if report.failed:
        out(f"\nНе применилось: {', '.join(report.failed)} — см. вывод выше.")
        return 1
    if dry_run:
        return 3 if report.pending else 0
    out("\nГотово. Перезапустите затронутые службы: nodectl restart <name>.")
    return 0
