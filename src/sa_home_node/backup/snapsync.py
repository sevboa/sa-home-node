from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sa_home_node.backup import sealed, serve
from sa_home_node.backup.config import BackupConfig
from sa_home_node.backup.snapdoc import ACTION_GET_SNAPSHOT, ACTION_SNAPSHOT_POKE, SnapshotError
from sa_home_node.backup.snapsource import SnapshotSource
from sa_home_node.backup.spec import BackupSpec
from sa_home_node.backup.store import BackupStore, backups_dir
from sa_home_node.proto.describe import ActionParam, ActionSpec
from sa_home_node.proto.wire import ERR_BAD_REQUEST, ProtoError

log = logging.getLogger(__name__)

Ask = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class SnapshotReceiver:
    def __init__(self, spec: BackupSpec, backup: BackupConfig, store: Any, ask: Ask) -> None:
        self._format = spec.snapshot_format
        self._partner = backup.partner.strip()
        self._poll = backup.snapshot_poll_s
        self._store = store
        self._ask = ask
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    def poke(self) -> None:
        self._wake.set()

    async def pull_once(self) -> bool:
        stored = self._store.load_snapshot(self._partner)
        have = stored.meta.get("hash") if stored else None
        resp = await self._ask(ACTION_GET_SNAPSHOT, {"have_hash": have})
        if resp.get("unchanged"):
            return False
        try:
            blob = base64.b64decode(resp["sealed"], validate=True)
            meta = dict(resp["meta"])
        except (KeyError, TypeError, ValueError, binascii.Error) as exc:
            raise SnapshotError(f"ответ напарника повреждён: {exc}") from exc
        if meta.get("source") != self._partner or meta.get("format") != self._format:
            raise SnapshotError(f"снапшот от {meta.get('source')!r}, ждали {self._partner!r}")
        if not meta.get("hash") or meta["hash"] == have:
            return False
        self._store.save_snapshot(self._partner, blob, meta)
        return True

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="backup-snapshot-pull")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._poll)
            self._wake.clear()
            try:
                await self.pull_once()
            except Exception as exc:  # noqa: BLE001
                log.debug("Бэкап снапшота: не удалось забрать у %s (%s)", self._partner, exc)


def snapshot_enabled(backup: BackupConfig) -> bool:
    return bool(backup.recipient_public_key.strip() and backup.partner.strip())


class SnapshotBackup:
    def __init__(
        self,
        source: SnapshotSource | None,
        receiver: SnapshotReceiver | None,
        *,
        store: Any = None,
        partner: str = "",
    ) -> None:
        self.source = source
        self.receiver = receiver
        self.store = store
        self.partner = partner

    def action_specs(self) -> list[ActionSpec]:
        specs: list[ActionSpec] = []
        if self.source is not None:
            specs.append(
                ActionSpec(
                    id=ACTION_GET_SNAPSHOT,
                    title="💾 Запечатанный снапшот БД",
                    params=(
                        ActionParam(
                            name="have_hash", type="string", required=False,
                            title="Хеш у запросившего",
                        ),
                    ),
                )
            )
        if self.receiver is not None:
            specs.append(ActionSpec(id=ACTION_SNAPSHOT_POKE, title="💾 Есть новый снапшот"))
        if self.store is not None:
            specs.extend(serve.action_specs())
        return specs

    def touch(self) -> None:
        if self.source is not None:
            self.source.touch()

    async def handle(self, action: str, args: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return await self._handle(action, args)
        except (SnapshotError, serve.ServeError) as exc:
            raise ProtoError(ERR_BAD_REQUEST, str(exc)) from exc

    async def _handle(self, action: str, args: dict[str, Any]) -> dict[str, Any] | None:
        if action == ACTION_GET_SNAPSHOT and self.source is not None:
            return await self.source.handle_get(args)
        if action == ACTION_SNAPSHOT_POKE and self.receiver is not None:
            self.receiver.poke()
            return {"ok": True}
        if self.store is not None:
            return serve.handle(self.store, self.partner, action, args)
        return None

    async def start(self) -> None:
        if self.receiver is not None:
            await self.receiver.start()

    async def stop(self) -> None:
        if self.source is not None:
            await self.source.stop()
        if self.receiver is not None:
            await self.receiver.stop()


def build_backup(
    spec: BackupSpec,
    backup: BackupConfig,
    state_path: str | Path,
    conn_getter: Callable[[], Any],
    node_id: str,
    ask_partner: Ask,
) -> SnapshotBackup | None:
    partner = backup.partner.strip()
    if not partner:
        return None
    source = None
    if snapshot_enabled(backup):
        try:

            async def poke() -> object:
                return await ask_partner(ACTION_SNAPSHOT_POKE, {})

            source = SnapshotSource(spec, backup, state_path, conn_getter, node_id, notify=poke)
        except sealed.SealedError as exc:
            log.warning("Бэкап снапшота выключен: [backup].recipient_public_key негоден (%s)", exc)
    store = BackupStore(backups_dir(state_path), spec.primary_table)
    return SnapshotBackup(
        source,
        SnapshotReceiver(spec, backup, store, ask_partner),
        store=store,
        partner=partner,
    )
