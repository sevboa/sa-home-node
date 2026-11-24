from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from sa_home_node.backup.hold import allow_publish
from sa_home_node.backup.spec import ApplyContext, BackupSpec, PrivilegedIO
from sa_home_node.config import NodeConfig


class ApplyError(Exception):
    pass


async def count_peers(conn: Any, table: str) -> int:
    cur = await conn.execute("SELECT name FROM sqlite_master WHERE name = ?", (table,))
    if await cur.fetchone() is None:
        return 0
    cur = await conn.execute(f"SELECT COUNT(*) FROM {table}")
    return int((await cur.fetchone())[0])


async def import_snapshot(
    conn: Any,
    snapshot: dict[str, Any],
    tables: tuple[str, ...],
    skip: frozenset[str],
    *,
    wipe: bool,
) -> dict[str, int]:
    done: dict[str, int] = {}
    data = snapshot.get("tables", {})
    for table in tables:
        if table in skip or table not in data:
            continue
        cur = await conn.execute(f"PRAGMA table_info({table})")
        known = {row[1] for row in await cur.fetchall()}
        if not known:
            continue
        columns = data[table]["columns"]
        idx = [i for i, name in enumerate(columns) if name in known]
        names = [columns[i] for i in idx]
        if wipe:
            await conn.execute(f"DELETE FROM {table}")
        sql = (
            f"INSERT OR REPLACE INTO {table} ({', '.join(names)}) "
            f"VALUES ({', '.join('?' for _ in names)})"
        )
        for row in data[table]["rows"]:
            await conn.execute(sql, [row[i] for i in idx])
        done[table] = len(data[table]["rows"])
    return done


def check_bundle(spec: BackupSpec, bundle: dict[str, Any], node_id: str) -> None:
    if bundle.get("node") != node_id:
        raise ApplyError(f"бандл для ноды {bundle.get('node')!r}, а здесь [node].id = {node_id!r}")
    if bundle.get("identity", {}).get("format") != spec.identity_format:
        raise ApplyError("в бандле нет identity")


async def apply_bundle(
    spec: BackupSpec,
    bundle: dict[str, Any],
    config: NodeConfig,
    *,
    config_path: Path,
    io: PrivilegedIO,
    log: Callable[[str], None] = print,
    wipe_db: bool = False,
) -> None:
    check_bundle(spec, bundle, config.node.id)
    config_path = Path(config_path)
    if not config_path.exists():
        raise ApplyError(f"нет конфига ноды {config_path}")
    await spec.apply(ApplyContext(bundle, config, config_path, io, log, wipe_db))
    marker = allow_publish(config.state_file, f"restore из бандла {bundle.get('identity_label')}")
    log(f"маркер публикации бэкапа: {marker}")
