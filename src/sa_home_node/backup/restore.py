from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from typing import Any

from sa_home_node.backup import package, snapdoc
from sa_home_node.backup.bundle import RestoreError
from sa_home_node.backup.serve import ACTION_STORE_GET, ACTION_STORE_LIST
from sa_home_node.backup.spec import BackupSpec, IdentityError

Ask = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


async def list_versions(ask: Ask, node: str) -> dict[str, Any]:
    return await ask(ACTION_STORE_LIST, {"node": node})


async def _fetch(ask: Ask, node: str, kind: str, label: str) -> tuple[bytes, dict]:
    resp = await ask(ACTION_STORE_GET, {"node": node, "kind": kind, "label": label})
    try:
        return base64.b64decode(resp["sealed"], validate=True), dict(resp.get("meta") or {})
    except (KeyError, TypeError, ValueError) as exc:
        raise RestoreError(f"ответ хранителя повреждён: {exc}") from exc


def pick_snapshot(versions: list[dict], requested: str | None, primary_table: str) -> str | None:
    by_label = {v["label"]: v for v in versions}
    if requested:
        if requested not in by_label:
            have = ", ".join(by_label) or "нет"
            raise RestoreError(f"нет снапшота {requested!r} (есть: {have})")
        return requested
    latest = by_label.get("latest")
    if latest is None:
        return None
    if latest["meta"].get("empty") and "last_nonempty" in by_label:
        rows = (by_label["last_nonempty"]["meta"].get("rows") or {}).get(primary_table, "?")
        raise RestoreError(
            f"снапшот latest пустой (таблица {primary_table} без строк), а последний непустой есть "
            f"(строк: {rows}). Выберите явно: --snapshot last_nonempty "
            "или --snapshot latest, если пустая БД — это то, что нужно"
        )
    return "latest"


async def fetch_bundle(
    spec: BackupSpec,
    ask: Ask,
    node: str,
    private_key: bytes,
    *,
    identity_label: str = "latest",
    snapshot_label: str | None = None,
    with_snapshot: bool = True,
) -> dict[str, Any]:
    listing = await list_versions(ask, node)
    if not any(v["label"] == identity_label for v in listing.get("identity", [])):
        raise RestoreError(f"у хранителя нет identity {identity_label!r} для {node}")
    blob, id_meta = await _fetch(ask, node, "identity", identity_label)
    try:
        identity = package.open_identity(private_key, blob)
    except IdentityError as exc:
        raise RestoreError(str(exc)) from exc
    snap_doc = snap_meta = None
    label = None
    if with_snapshot:
        label = pick_snapshot(listing.get("snapshot", []), snapshot_label, spec.primary_table)
        if label is not None:
            sblob, snap_meta = await _fetch(ask, node, "snapshot", label)
            try:
                snap_doc = snapdoc.open_snapshot(spec.snapshot_format, private_key, sblob)
            except snapdoc.SnapshotError as exc:
                raise RestoreError(str(exc)) from exc
            if snap_doc.get("node") != node:
                raise RestoreError(f"снапшот принадлежит ноде {snap_doc.get('node')!r}, а не {node!r}")
    return {
        "format": spec.bundle_format,
        "node": node,
        "identity_label": identity_label,
        "snapshot_label": label,
        "identity_meta": id_meta,
        "snapshot_meta": snap_meta,
        "identity": identity,
        "snapshot": snap_doc,
    }


def summarize_bundle(spec: BackupSpec, bundle: dict[str, Any]) -> str:
    lines = [f"Нода: {bundle['node']}", f"Identity: {bundle['identity_label']}"]
    meta = bundle.get("identity_meta") or {}
    if meta.get("updated_at"):
        lines.append(f"  собрана источником: {meta['updated_at']}")
    lines.extend(spec.summarize(bundle))
    snap = bundle.get("snapshot")
    if snap is None:
        lines.append("Снапшот БД: нет (будет восстановлена только identity)")
    else:
        smeta = bundle.get("snapshot_meta") or {}
        tables = snap["tables"]
        rows = {name: len(t["rows"]) for name, t in tables.items()}
        extra = "".join(f", {k} {v}" for k, v in spec.rows_extra(tables).items())
        lines.append(
            f"Снапшот БД: {bundle['snapshot_label']} (снят {smeta.get('taken_at', '?')}), "
            f"{spec.primary_table} {rows.get(spec.primary_table, 0)}{extra}"
        )
        if not rows.get(spec.primary_table):
            lines.append("  ВНИМАНИЕ: снапшот пустой — данные не вернуть")
        skipped = ", ".join(sorted(set(tables) & spec.skip_tables))
        lines.append(f"  по таблицам: {rows}; не переносятся: {skipped}")
    return "\n".join(lines)
