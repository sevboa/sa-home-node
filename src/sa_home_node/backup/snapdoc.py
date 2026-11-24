from __future__ import annotations

import json
import zlib
from collections.abc import Callable
from typing import Any

from sa_home_node.backup import sealed

MAX_SEALED_BYTES = 640 * 1024
MAX_PLAINTEXT_BYTES = 64 * 1024 * 1024
ACTION_GET_SNAPSHOT = "backup_snapshot_get"
ACTION_SNAPSHOT_POKE = "backup_snapshot_poke"


class SnapshotError(Exception):
    pass


async def dump_tables(conn: Any, tables: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for table in tables:
        cur = await conn.execute(f"PRAGMA table_info({table})")
        info = await cur.fetchall()
        if not info:
            continue
        columns = [r[1] for r in info]
        pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
        order = ", ".join(pk) if pk else "rowid"
        cur = await conn.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order}")
        out[table] = {"columns": columns, "rows": [list(r) for r in await cur.fetchall()]}
    return out


def build_document(format: str, node: str, tables: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"format": format, "node": node, "tables": tables}


def summarize(
    doc: dict[str, Any],
    primary_table: str,
    extra: Callable[[dict[str, Any]], dict[str, int]] | None = None,
) -> dict[str, int]:
    tables = doc.get("tables", {})
    rows = {name: len(t["rows"]) for name, t in tables.items()}
    rows.setdefault(primary_table, 0)
    if extra is not None:
        rows.update(extra(tables))
    return rows


def seal_document(recipient_public_key: bytes, plaintext: bytes) -> bytes:
    return sealed.seal(recipient_public_key, zlib.compress(plaintext, 9))


def open_snapshot(format: str, private_key: bytes, blob: bytes) -> dict[str, Any]:
    try:
        packed = sealed.open_sealed(private_key, blob)
        plaintext = zlib.decompressobj().decompress(packed, MAX_PLAINTEXT_BYTES)
        doc = json.loads(plaintext)
    except (sealed.SealedError, zlib.error, ValueError) as exc:
        raise SnapshotError(f"не удалось расшифровать снапшот: {exc}") from exc
    if doc.get("format") != format:
        raise SnapshotError(f"неизвестный формат снапшота: {doc.get('format')!r}")
    return doc
