from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from sa_home_node.backup.apply import ApplyError, apply_bundle
from sa_home_node.backup.bundle import RestoreError, load_bundle
from sa_home_node.backup.hold import allow_publish
from sa_home_node.backup.privio import SudoIO
from sa_home_node.backup.restore import summarize_bundle
from sa_home_node.backup.spec import BackupSpec
from sa_home_node.config import NodeConfig
from sa_home_node.files import atomic_write_bytes
from sa_home_node.plugins import Plugin

YES = ("y", "yes", "д")


def staged_bundle_path(state_path: str | Path) -> Path:
    return Path(state_path).parent / "restore-bundle.json"


def run_stage(config: NodeConfig, stdin: Any = None) -> int:
    data = (stdin or sys.stdin.buffer).read()
    if not data.strip():
        print("Пустой ввод: бандл не получен.", file=sys.stderr)
        return 1
    path = staged_bundle_path(config.state_file)
    atomic_write_bytes(path, data)
    print(f"Бандл принят: {path}")
    return 0


def _load(path: Path, plugins: Sequence[Plugin]) -> tuple[BackupSpec, dict[str, Any]]:
    error = RestoreError("нет плагина с бэкапом")
    for plugin in plugins:
        spec = plugin.backup
        if spec is None:
            continue
        try:
            return spec, load_bundle(path, spec.bundle_format)
        except RestoreError as exc:
            error = exc
    raise error


def run_apply(
    config: NodeConfig,
    plugins: Sequence[Plugin],
    *,
    bundle: str | Path | None,
    yes: bool,
    wipe_db: bool,
    ask: Callable[[str], str] = input,
) -> int:
    staged = staged_bundle_path(config.state_file)
    bundle_path = Path(bundle) if bundle else staged
    try:
        spec, data = _load(bundle_path, plugins)
        print(summarize_bundle(spec, data))
        if not yes:
            print("\nНода (служба vpn и sa-home-node) должна быть остановлена.")
            if ask("Применить на этой ноде? [y/N] ").strip().lower() not in YES:
                print("Отменено.")
                return 1
        asyncio.run(
            apply_bundle(spec, data, config, config_path=config.path, io=SudoIO(), wipe_db=wipe_db)
        )
    except (RestoreError, ApplyError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Не удалось применить: {exc}", file=sys.stderr)
        return 1
    if bundle_path.is_file() and bundle_path == staged:
        bundle_path.unlink(missing_ok=True)
    print("\nГотово. Дальше: nodectl fix и запуск ноды.")
    return 0


def run_release(config: NodeConfig) -> int:
    path = allow_publish(config.state_file, "backup-release")
    print(f"Публикация бэкапа разрешена: {path}")
    return 0
