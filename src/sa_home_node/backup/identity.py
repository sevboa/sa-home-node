from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sa_home_node.backup import package, sealed
from sa_home_node.backup.config import BackupConfig
from sa_home_node.backup.hold import HOLD_HINT, publish_allowed
from sa_home_node.backup.spec import BackupContext, BackupSpec, IdentityError
from sa_home_node.config import NodeConfig
from sa_home_node.files import atomic_write_bytes
from sa_home_node.plugins import ReplicationHooks
from sa_home_node.services.instances import InstanceMeta, InstanceStore

if TYPE_CHECKING:
    from sa_home_node.backup.store import BackupStore

log = logging.getLogger(__name__)

SRC_SUFFIX = ".src.json"


def _read_src_hash(path: Path) -> str:
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("plain_hash", ""))
    except (OSError, ValueError):
        return ""


class IdentityPublisher:
    def __init__(
        self,
        spec: BackupSpec,
        backup: BackupConfig,
        store: InstanceStore,
        node_id: str,
        state_path: str | Path,
        config: NodeConfig,
    ) -> None:
        self._spec = spec
        self._backup = backup
        self._store = store
        self._node_id = node_id
        self._state_path = state_path
        self._config = config
        self._recipient = sealed.load_key(backup.recipient_public_key)
        self._held_logged = False

    def _src_path(self) -> Path:
        pkg = self._store.package_path(self._spec.identity_service, self._node_id)
        return pkg.with_name(f"{pkg.name.removesuffix('.toml')}{SRC_SUFFIX}")

    async def publish_if_changed(self) -> bool:
        if not publish_allowed(self._state_path):
            if not self._held_logged:
                log.warning("Бэкап identity: %s", HOLD_HINT)
                self._held_logged = True
            return False
        try:
            doc = await self._spec.collect_identity(
                BackupContext(config=self._config, backup=self._backup, node_id=self._node_id)
            )
        except IdentityError as exc:
            log.warning("Бэкап identity: %s — пакет не обновлён", exc)
            return False
        plaintext = package.canonical_bytes(doc)
        digest = package.plain_hash(plaintext)
        pkg, src = self._store.package_path(self._spec.identity_service, self._node_id), self._src_path()
        if pkg.exists() and _read_src_hash(src) == digest:
            return False
        blob = sealed.seal(self._recipient, plaintext)
        atomic_write_bytes(pkg, package.render_package(self._spec.identity_format, self._node_id, blob))
        atomic_write_bytes(src, json.dumps({"plain_hash": digest}).encode("utf-8"))
        return True


def identity_hooks(
    spec: BackupSpec,
    backup: BackupConfig,
    store: InstanceStore,
    backups: BackupStore,
    node_id: str,
    state_path: str | Path,
    config: NodeConfig,
    *,
    publishing: bool,
) -> ReplicationHooks | None:
    partner = backup.partner.strip()
    if not partner:
        return None

    def passive(service: str, instance: str) -> bool:
        return service == spec.identity_service and instance == partner

    def on_applied(meta: InstanceMeta, data: bytes) -> None:
        if meta.service != spec.identity_service or meta.instance != partner:
            return
        source, blob = package.parse_package(spec.identity_format, data)
        if source != meta.instance:
            raise IdentityError(f"пакет {meta.instance} заявляет источник {source}")
        backups.save_identity(source, blob, meta)

    before_announce = None
    if publishing:
        try:
            publisher = IdentityPublisher(spec, backup, store, node_id, state_path, config)
        except sealed.SealedError as exc:
            log.warning("Бэкап identity выключен: [backup].recipient_public_key негоден (%s)", exc)
        else:
            before_announce = publisher.publish_if_changed
    return ReplicationHooks(passive=passive, before_announce=before_announce, on_applied=on_applied)
