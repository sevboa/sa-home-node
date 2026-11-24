import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from sa_home_node.backup import bundle as bundles
from sa_home_node.backup import local
from sa_home_node.backup import restore as rs
from sa_home_node.backup.bundle import RestoreError
from sa_home_node.backup.config import BackupConfig
from sa_home_node.backup.sealed import (
    SealedError,
    dump_key,
    generate_keypair,
    read_private_key,
    write_private_key,
)
from sa_home_node.backup.serve import ACTION_STORE_LIST
from sa_home_node.backup.spec import BackupSpec
from sa_home_node.config import ConfigError, NodeConfig, load_config
from sa_home_node.plugins import Plugin, PluginError, load_plugins
from sa_home_node.proto.client import ProtoClient
from sa_home_node.proto.endpoints import resolve_endpoint
from sa_home_node.proto.wire import Address, ProtoError
from sa_home_node.swarm.config import swarm_section

HOLDER_HELP = "нода-хранитель копии (по умолчанию — ищем среди живых пиров)"
DEFAULT_KEY_PATH = "~/.config/sa-home-bot/backup.key"
ASK_TIMEOUT = 30.0


def add_backup_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("backup", help="бэкап identity и снапшотов служб")
    parser.add_argument("--config", type=Path, default=None, help="путь к config.toml")
    sub = parser.add_subparsers(dest="backup_command", required=True)
    kg = sub.add_parser("keygen", help="сгенерировать пару ключей получателя бэкапов")
    kg.add_argument(
        "--out",
        default=DEFAULT_KEY_PATH,
        help=f"куда записать приватный ключ (0600), по умолчанию {DEFAULT_KEY_PATH}",
    )
    ls = sub.add_parser("list", help="какие копии ноды лежат у напарника")
    ls.add_argument("node", help="id ноды, чью копию смотрим")
    ls.add_argument("--holder", default=None, help=HOLDER_HELP)
    rs_parser = sub.add_parser("restore", help="восстановить службу ноды из копии напарника")
    rs_parser.add_argument("node", help="id восстанавливаемой ноды")
    mode = rs_parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        metavar="КАТАЛОГ",
        help="только расшифровать и разложить в каталог (0700/0600), ноды не трогать",
    )
    mode.add_argument(
        "--apply", action="store_true", help="применить на целевой ноде по ssh (интерактивный sudo там)"
    )
    rs_parser.add_argument(
        "--snapshot",
        default=None,
        metavar="last_nonempty|latest|МЕТКА",
        help="какой снапшот БД (по умолчанию latest; пустой latest — отказ)",
    )
    rs_parser.add_argument(
        "--identity", default="latest", metavar="latest|МЕТКА", help="какая identity (метки — из `backup list`)"
    )
    rs_parser.add_argument("--no-snapshot", action="store_true", help="только identity, без БД")
    rs_parser.add_argument("--holder", default=None, help=HOLDER_HELP)
    rs_parser.add_argument("--ssh-host", default=None, help="куда ssh (по умолчанию — id ноды)")
    rs_parser.add_argument("--remote-nodectl", default=None, help="путь к nodectl на целевой ноде")
    rs_parser.add_argument(
        "--wipe-db", action="store_true", help="на цели БД уже не пуста — очистить и залить заново"
    )
    rs_parser.add_argument("--yes", "-y", action="store_true", help="без вопроса о подтверждении")
    sub.add_parser("stage", help="принять бандл восстановления со stdin (0600)")
    ap = sub.add_parser("apply", help="применить бандл восстановления (интерактивный sudo, локально)")
    ap.add_argument(
        "--bundle", default=None, help="файл бандла или каталог --dry-run (по умолчанию — принятый stage)"
    )
    ap.add_argument("--yes", "-y", action="store_true", help="без вопроса о подтверждении")
    ap.add_argument(
        "--wipe-db", action="store_true", help="БД уже не пуста — очистить таблицы снапшота и залить заново"
    )
    sub.add_parser("release", help="разрешить этой ноде публиковать бэкап напарнику")
    parser.set_defaults(_run=run)


def run(args: argparse.Namespace) -> int:
    if args.backup_command == "keygen":
        return _keygen(args)
    try:
        config = load_config(args.config)
        plugins = _plugins(config)
        if args.backup_command == "stage":
            return local.run_stage(config)
        if args.backup_command == "release":
            return local.run_release(config)
        if args.backup_command == "apply":
            return local.run_apply(
                config, plugins, bundle=args.bundle, yes=args.yes, wipe_db=args.wipe_db
            )
        return asyncio.run(_run_remote(args, config, plugins[0].backup))
    except (ConfigError, PluginError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    except (RestoreError, SealedError, ProtoError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except (ConnectionError, OSError, TimeoutError) as exc:
        print(f"Нет связи с нодой/хранителем: {exc}", file=sys.stderr)
        return 1


def _plugins(config: NodeConfig) -> list[Plugin]:
    plugins = [p for p in load_plugins(config.node.plugins) if p.backup is not None]
    if not plugins:
        raise RestoreError("нет плагина с бэкапом")
    return plugins


def _load_private(config: NodeConfig) -> bytes:
    path = config.section("backup", BackupConfig).private_key_file.strip()
    if not path:
        raise SealedError("[backup].private_key_file не задан — расшифровывать нечем")
    return read_private_key(Path(path))


async def _open_ask(config: NodeConfig, spec: BackupSpec, holder: str | None, node: str):
    client = ProtoClient(
        resolve_endpoint(config.node.socket, config.path.parent), token=swarm_section(config).token
    )
    await client.connect()

    def asker(name: str):
        async def ask(action: str, args: dict[str, Any]) -> dict[str, Any]:
            return await client.command(
                action, args, dst=Address(node=name, service=spec.service), timeout=ASK_TIMEOUT
            )

        return ask

    if holder:
        return client, asker(holder), holder
    try:
        state = await client.get_state()
        candidates = [p["id"] for p in state.get("peers", []) if p.get("alive") and p["id"] != node]
        for name in candidates:
            try:
                await asker(name)(ACTION_STORE_LIST, {"node": node})
            except (ProtoError, TimeoutError):
                continue
            return client, asker(name), name
    except BaseException:
        await client.close()
        raise
    await client.close()
    raise RestoreError(
        f"не нашёл живого хранителя копии {node} среди пиров {candidates or 'нет'} — укажите --holder"
    )


def _format_versions(listing: dict[str, Any], primary_table: str) -> str:
    lines = []
    for title, key, date_key in (("identity", "identity", "updated_at"), ("снапшот БД", "snapshot", "taken_at")):
        lines.append(f"{title}:")
        for v in listing.get(key, []):
            m = v["meta"]
            extra = ""
            if key == "snapshot":
                rows = m.get("rows") or {}
                extra = f"  строк {rows.get(primary_table, '?')}" + ("  ПУСТОЙ" if m.get("empty") else "")
            when = m.get(date_key) or m.get("stored_at") or "?"
            lines.append(f"  {v['label']:34} {when}{extra}")
        if not listing.get(key):
            lines.append("  —")
    return "\n".join(lines)


async def _run_remote(args: argparse.Namespace, config: NodeConfig, spec: BackupSpec) -> int:
    client, ask, holder = await _open_ask(config, spec, args.holder, args.node)
    try:
        if args.backup_command == "list":
            listing = await rs.list_versions(ask, args.node)
            print(f"Копии ноды {args.node} у {holder}:\n" + _format_versions(listing, spec.primary_table))
            return 0
        bundle = await rs.fetch_bundle(
            spec,
            ask,
            args.node,
            _load_private(config),
            identity_label=args.identity,
            snapshot_label=args.snapshot,
            with_snapshot=not args.no_snapshot,
        )
    finally:
        await client.close()
    print(rs.summarize_bundle(spec, bundle))
    if args.dry_run:
        target = Path(args.dry_run).expanduser()
        written = bundles.write_dir(target, bundle, rs.summarize_bundle(spec, bundle))
        print(
            f"\nРазложено в {target}: " + ", ".join(p.name for p in written) + " (каталог 0700, файлы 0600)."
            "\nНоды не тронуты. Удалите каталог после проверки: там приватные ключи."
        )
        return 0
    return _apply_remote(args, bundle)


def _apply_remote(args: argparse.Namespace, bundle: dict[str, Any]) -> int:
    host = args.ssh_host or args.node
    remote = args.remote_nodectl or bundles.REMOTE_NODECTL
    print(f"\nЦель: ssh {host}. Бандл (с приватными ключами) уйдёт по ssh, на цели — 0600.")
    if not args.yes and input("Применить? [y/N] ").strip().lower() not in local.YES:
        print("Отменено.")
        return 1
    bundles.stage_over_ssh(host, bundles.bundle_to_bytes(bundle), remote)
    code = bundles.apply_over_ssh(host, remote, "--wipe-db" if args.wipe_db else "")
    if code != 0:
        print(
            f"backup apply на {host} завершился кодом {code}; бандл остался на цели "
            "(повторить: ssh -t … nodectl backup apply), удалите его после.",
            file=sys.stderr,
        )
    return code


def _keygen(args: argparse.Namespace) -> int:
    out = Path(args.out).expanduser()
    if out.exists():
        print(f"Файл {out} уже существует — не затираю (удалите вручную, если нужно).", file=sys.stderr)
        return 1
    private, public = generate_keypair()
    try:
        write_private_key(out, private)
    except (OSError, SealedError) as exc:
        print(f"Не удалось записать ключ: {exc}", file=sys.stderr)
        return 1
    print(f"Приватный ключ записан в {out} (права 0600). Храните только на машине получателя:")
    print(f'  [backup]\n  private_key_file = "{out}"')
    print("Публичный ключ — для нод-источников бэкапа:")
    print(f'  [backup]\n  recipient_public_key = "{dump_key(public)}"')
    return 0
