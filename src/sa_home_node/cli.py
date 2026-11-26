import argparse
import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from sa_home_node.backup.cli import add_backup_parser
from sa_home_node.backup.cli import run as run_backup
from sa_home_node.config import ConfigError
from sa_home_node.content import ContentError
from sa_home_node.ctl import run as ctl_run
from sa_home_node.ctl.cli import build_parser as build_ctl_parser
from sa_home_node.fixups.runner import fix_command
from sa_home_node.host import current
from sa_home_node.node import run_node, run_service
from sa_home_node.plugins import PluginError, available
from sa_home_node.wizard.run import add_init_parser, run_init


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "ctl":
        ctl_args = build_ctl_parser("sa-home-node ctl").parse_args(argv[1:])
        return asyncio.run(ctl_run.run(ctl_args))

    parser = argparse.ArgumentParser(prog="sa-home-node", description="Управление нодой sa-home-node")
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="запуск ноды")
    run_parser.add_argument("--config", type=Path, default=None, help="путь к config.toml")
    service_parser = subparsers.add_parser("service", help="запуск процессной службы")
    service_parser.add_argument("name", help="имя службы")
    service_parser.add_argument("--instance", default="", help="имя экземпляра")
    service_parser.add_argument("--config", type=Path, default=None, help="путь к config.toml")
    fix_parser = subparsers.add_parser("fix", help="применить фиксапы")
    fix_parser.add_argument("--config", type=Path, default=None, help="путь к config.toml")
    fix_parser.add_argument("--list", dest="list_only", action="store_true", help="показать список фиксапов")
    fix_parser.add_argument("--dry-run", action="store_true", help="только проверить, ничего не применять")
    fix_parser.add_argument("--only", default="", help="id фиксапов через запятую")
    subparsers.add_parser("plugins", help="список установленных плагинов")
    add_backup_parser(subparsers)
    subparsers.add_parser("ctl", help="консоль управления нодой")
    add_init_parser(subparsers)
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    if args.command == "plugins":
        names = sorted(available())
        if not names:
            print("плагины не установлены")
        else:
            for name in names:
                print(name)
        return 0
    if args.command == "backup":
        return run_backup(args)
    if args.command == "fix":
        only = tuple(fid for fid in args.only.split(",") if fid)
        return fix_command(args.config, dry_run=args.dry_run, only=only, list_only=args.list_only)
    if args.command == "init":
        interactive = sys.stdin.isatty() and not args.non_interactive
        host = current()
        executable = str(Path(sys.argv[0]).resolve())

        def run_step(cmd_argv: list[str]) -> bool:
            result = subprocess.run(cmd_argv, check=False)
            return result.returncode == 0

        def fix(path: Path) -> int:
            return fix_command(path, dry_run=False, only=(), list_only=False)

        return run_init(
            args,
            reply=input,
            interactive=interactive,
            host=host,
            run_step=run_step,
            fix=fix,
            executable=executable,
        )
    logging.basicConfig(level=logging.INFO)
    try:
        if args.command == "run":
            if asyncio.run(run_node(args.config)):
                return current().self_restart(sys.argv)
        else:
            return asyncio.run(run_service(args.name, args.instance, args.config))
    except (ConfigError, ContentError, PluginError) as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2
    return 0
