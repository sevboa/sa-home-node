from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sa_home_node.backup import sealed, snapdoc
from sa_home_node.backup.config import BackupConfig
from sa_home_node.backup.hold import HOLD_HINT, publish_allowed
from sa_home_node.backup.package import canonical_bytes, plain_hash
from sa_home_node.backup.snapdoc import MAX_SEALED_BYTES, SnapshotError
from sa_home_node.backup.spec import BackupSpec
from sa_home_node.services.periodic import PEER_ERRORS

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Snapshot:
    blob: bytes
    meta: dict[str, Any]


class SnapshotSource:
    def __init__(
        self,
        spec: BackupSpec,
        backup: BackupConfig,
        state_path: str | Path,
        conn_getter: Callable[[], Any],
        node_id: str,
        *,
        notify: Callable[[], Awaitable[object]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._spec = spec
        self._state_path = state_path
        self._conn_getter = conn_getter
        self._node_id = node_id
        self._recipient = sealed.load_key(backup.recipient_public_key)
        self._interval = backup.snapshot_interval_s
        self._debounce = backup.snapshot_debounce_s
        self._notify = notify
        self._clock = clock
        self._cached: Snapshot | None = None
        self._built_at = 0.0
        self._rev = 0
        self._dirty = False
        self._lock = asyncio.Lock()
        self._pending: asyncio.Task | None = None

    def _due(self) -> bool:
        if self._cached is None:
            return True
        age = self._clock() - self._built_at
        return age >= self._interval or (self._dirty and age >= self._debounce)

    async def current(self) -> Snapshot | None:
        async with self._lock:
            if self._due():
                try:
                    await self._rebuild()
                except Exception:
                    log.exception("Бэкап снапшота %s: сбой сборки", self._spec.service)
            return self._cached

    async def _rebuild(self) -> None:
        spec = self._spec
        tables = await snapdoc.dump_tables(self._conn_getter(), spec.tables)
        doc = snapdoc.build_document(spec.snapshot_format, self._node_id, tables)
        plaintext = canonical_bytes(doc)
        digest = plain_hash(plaintext)
        self._built_at = self._clock()
        self._dirty = False
        if self._cached is not None and self._cached.meta["hash"] == digest:
            return
        blob = snapdoc.seal_document(self._recipient, plaintext)
        if len(blob) > MAX_SEALED_BYTES:
            raise SnapshotError(f"снапшот {len(blob)} Б не лезет в сообщение протокола")
        self._rev += 1
        self._cached = Snapshot(
            blob=blob,
            meta={
                "source": self._node_id,
                "format": spec.snapshot_format,
                "rev": self._rev,
                "hash": digest,
                "taken_at": datetime.now(tz=UTC).isoformat(),
                "rows": snapdoc.summarize(doc, spec.primary_table, spec.rows_extra),
            },
        )

    def touch(self) -> None:
        self._dirty = True
        if self._pending is None or self._pending.done():
            self._pending = asyncio.create_task(self._flush(), name="snapshot-flush")

    async def _flush(self) -> None:
        await asyncio.sleep(self._debounce)
        snap = await self.current()
        if snap is not None and self._notify is not None:
            try:
                await self._notify()
            except PEER_ERRORS as exc:
                log.debug("Бэкап снапшота %s: напарник не уведомлён (%s)", self._spec.service, exc)

    async def handle_get(self, args: dict[str, Any]) -> dict[str, Any]:
        if not publish_allowed(self._state_path):
            raise SnapshotError(HOLD_HINT)
        snap = await self.current()
        if snap is None:
            raise SnapshotError("снапшот ещё не собран")
        if args.get("have_hash") == snap.meta["hash"]:
            return {"unchanged": True, "hash": snap.meta["hash"]}
        return {
            "hash": snap.meta["hash"],
            "meta": snap.meta,
            "sealed": base64.b64encode(snap.blob).decode("ascii"),
        }

    async def stop(self) -> None:
        if self._pending is not None:
            self._pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pending
            self._pending = None
