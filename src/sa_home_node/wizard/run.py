from __future__ import annotations

import argparse
import secrets
import socket
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

from sa_home_node.config import DEFAULT_CONFIG
from sa_home_node.swarm.kind import NodeKind
from sa_home_node.wizard.ask import (
    resolve_assignments,
    resolve_join,
    resolve_kind,
    resolve_listen,
    resolve_token,
)
from sa_home_node.wizard.files import NodeAnswers, render_config, render_systemd_unit

DEFAULT_DATA_DIR = Path("~/sa-home-node")


def add_init_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("init", help="Создать конфиг и настроить ноду")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Путь к конфигу")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Каталог данных")
    parser.add_argument("--node-id", help="Имя ноды в рое")
    parser.add_argument("--kind", choices=get_args(NodeKind), help="Тип машины")
    parser.add_argument("--assignments", help="Назначения через запятую")
    parser.add_argument("--listen", help="Адреса для входящих соединений через запятую")
    parser.add_argument("--join", help="IP:порт существующего соседа")
    parser.add_argument("--token", help="Токен роя")
    parser.add_argument("--non-interactive", action="store_true", help="Не спрашивать, использовать дефолты")
    parser.add_argument("--force", action="store_true", help="Перезаписать существующие файлы")
    parser.add_argument("--no-unit", action="store_true", help="Не писать системный юнит")
    parser.add_argument("--no-start", action="store_true", help="Не запускать службу после настройки")


def run_init(
    args: argparse.Namespace,
    *,
    reply: Callable[[str], str],
    interactive: bool,
    host: Any,
    run_step: Callable[[list[str]], bool],
    fix: Callable[[Path], int],
    executable: str,
) -> int:
    config_path = Path(args.config).expanduser()
    data_dir = Path(args.data_dir).expanduser()

    if config_path.exists() and not args.force:
        print(
            f"{config_path} уже существует — не трогаю (передай --force, чтобы переписать, "
            "или другой --config)",
            file=sys.stderr,
        )
        return 1

    node_id = args.node_id
    if node_id is None:
        default_id = socket.gethostname()
        node_id = (
            reply(f"Имя ноды в рое [{default_id}]: ").strip() or default_id if interactive else default_id
        )

    kind = resolve_kind(args.kind, interactive, reply)
    assignments = resolve_assignments(args.assignments, interactive, reply)
    listen = resolve_listen(args.listen, interactive, reply, kind)
    join, founding_new_swarm = resolve_join(args.join, interactive, reply)
    token = resolve_token(
        args.token,
        interactive,
        reply,
        join,
        founding_new_swarm,
        lambda: secrets.token_urlsafe(32),
    )

    if token is None:
        print(
            "Не хватает значений для --non-interactive: --token (обязателен вместе с --join)",
            file=sys.stderr,
        )
        return 2

    config_path.parent.mkdir(parents=True, exist_ok=True)
    answers = NodeAnswers(
        node_id=node_id,
        kind=kind,
        assignments=assignments,
        listen=listen,
        token=token,
        join=join,
    )
    config_path.write_text(render_config(answers, data_dir), encoding="utf-8")
    (data_dir / "data").mkdir(parents=True, exist_ok=True)
    print(f"Записан {config_path}")
    print(f"Каталог данных {data_dir}/data готов")

    unit = host.node_unit(config_path)
    unit_written = False
    if not args.no_unit and unit.format == "systemd-user":
        if unit.path.exists() and not args.force:
            print(f"{unit.path} уже существует — не трогаю (--force, чтобы переписать)")
        else:
            unit.path.parent.mkdir(parents=True, exist_ok=True)
            unit_content = render_systemd_unit(executable, config_path, data_dir)
            unit.path.write_text(unit_content, encoding="utf-8")
            print(f"Записан {unit.path}")
            unit_written = True

    if not join:
        print(f"\nТокен роя (сохрани — понадобится для следующих нод): {token}")

    if unit_written:
        if args.no_start:
            print(f"\nДальше:\n  {' '.join(unit.reload_argv)}\n  {' '.join(unit.enable_argv)}")
        else:
            run_step(unit.reload_argv)
            run_step(unit.enable_argv)
            if unit.linger_argv:
                if interactive:
                    run_step(host.elevate_argv(unit.linger_argv))
                else:
                    sudo_cmd = host.elevate_argv(unit.linger_argv)
                    print(f"\nНе в интерактивном режиме — выполни сам для linger:\n  {' '.join(sudo_cmd)}")
    else:
        print(
            f"\nЗапуск вручную (из каталога данных): cd {data_dir} && {executable} run --config {config_path}"
        )

    if interactive:
        fix(config_path)
    else:
        print("\nДонастройка (fixups) не запущена — не в интерактивном режиме.")
        print("Выполни отдельно на ноде: sa-home-node fix")

    return 0
