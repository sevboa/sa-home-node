from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sa_home_node.files import atomic_write_bytes
from sa_home_node.services.instances import InstanceMeta

log = logging.getLogger(__name__)

BACKUPS_DIRNAME = "backups"
IDENTITY_FILE = "identity.sealed"
IDENTITY_META_FILE = "identity.meta.json"
HISTORY_DIR = "history"
HISTORY_KEEP = 5

SNAPSHOTS_DIR = "snapshots"
SNAPSHOT_FILE = "latest.sealed"
SNAPSHOT_META_FILE = "latest.meta.json"
SNAPSHOT_NONEMPTY_FILE = "last_nonempty.sealed"
SNAPSHOT_NONEMPTY_META_FILE = "last_nonempty.meta.json"
SNAPSHOT_HISTORY_KEEP = 24

_NON_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def snapshot_is_empty(meta: dict, primary_table: str) -> bool:
    return int((meta.get("rows") or {}).get(primary_table, 0)) == 0


def _read_meta(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _meta_json(info: dict) -> bytes:
    return json.dumps(info, ensure_ascii=False, indent=2).encode("utf-8")


def backups_dir(state_path: str | Path) -> Path:
    return Path(state_path).parent / BACKUPS_DIRNAME


@dataclass(frozen=True)
class StoredIdentity:
    blob: bytes
    meta: dict


@dataclass(frozen=True)
class StoredSnapshot:
    blob: bytes
    meta: dict


def _meta_path(blob_path: Path) -> Path:
    return blob_path.with_name(blob_path.name[: -len(".sealed")] + ".meta.json")


def _history_label(blob_path: Path, prefix: str) -> str:
    return blob_path.name[len(prefix) + 1 : -len(".sealed")]


@dataclass(frozen=True)
class _VersionedSlot:
    directory: Path
    prefix: str
    latest_file: str
    latest_meta_file: str
    keep: int
    primary_table: str | None = None

    @property
    def latest(self) -> Path:
        return self.directory / self.latest_file

    @property
    def latest_meta(self) -> Path:
        return self.directory / self.latest_meta_file

    def history(self) -> list[Path]:
        history_dir = self.directory / HISTORY_DIR
        return sorted(history_dir.glob(f"{self.prefix}.*.sealed")) if history_dir.exists() else []

    def archive_latest(self) -> None:
        if not self.latest.exists():
            return
        stamp = _NON_FILENAME_CHARS.sub("_", _read_meta(self.latest_meta).get("stored_at", ""))
        stamp = stamp or datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%f")
        history_dir = self.directory / HISTORY_DIR
        atomic_write_bytes(history_dir / f"{self.prefix}.{stamp}.sealed", self.latest.read_bytes())
        if self.latest_meta.exists():
            atomic_write_bytes(
                history_dir / f"{self.prefix}.{stamp}.meta.json", self.latest_meta.read_bytes()
            )
        for old in self.history()[: -self.keep]:
            old.unlink(missing_ok=True)
            _meta_path(old).unlink(missing_ok=True)

    def _history_meta(self, path: Path) -> dict:
        meta = _read_meta(_meta_path(path))
        if self.primary_table is not None:
            meta.setdefault("empty", snapshot_is_empty(meta, self.primary_table))
        return meta

    def history_versions(self) -> list[dict]:
        return [
            {"label": _history_label(path, self.prefix), "meta": self._history_meta(path)}
            for path in reversed(self.history())
        ]

    def read_history(self, label: str) -> tuple[bytes, dict] | None:
        for path in self.history():
            if _history_label(path, self.prefix) == label:
                return path.read_bytes(), self._history_meta(path)
        return None


class BackupStore:
    def __init__(self, root: str | Path, primary_table: str = "") -> None:
        self.root = Path(root)
        self.primary_table = primary_table

    def node_dir(self, node: str) -> Path:
        return self.root / (_NON_FILENAME_CHARS.sub("_", node) or "_")

    def snapshots_dir(self, node: str) -> Path:
        return self.node_dir(node) / SNAPSHOTS_DIR

    def _identity_slot(self, node: str) -> _VersionedSlot:
        return _VersionedSlot(
            self.node_dir(node), "identity", IDENTITY_FILE, IDENTITY_META_FILE, HISTORY_KEEP
        )

    def _snapshot_slot(self, node: str) -> _VersionedSlot:
        return _VersionedSlot(
            self.snapshots_dir(node),
            "snapshot",
            SNAPSHOT_FILE,
            SNAPSHOT_META_FILE,
            SNAPSHOT_HISTORY_KEEP,
            self.primary_table or None,
        )

    def save_identity(self, node: str, blob: bytes, meta: InstanceMeta) -> None:
        slot = self._identity_slot(node)
        slot.archive_latest()
        info = {
            "source": node,
            "rev": meta.rev,
            "hash": meta.hash,
            "updated_at": meta.updated_at,
            "stored_at": datetime.now(tz=UTC).isoformat(),
        }
        atomic_write_bytes(slot.latest, blob)
        atomic_write_bytes(slot.latest_meta, _meta_json(info))
        log.info("Бэкап identity ноды %s сохранён (ревизия %d)", node, meta.rev)

    def load_identity(self, node: str) -> StoredIdentity | None:
        slot = self._identity_slot(node)
        if not slot.latest.exists():
            return None
        return StoredIdentity(blob=slot.latest.read_bytes(), meta=_read_meta(slot.latest_meta))

    def history(self, node: str) -> list[Path]:
        return self._identity_slot(node).history()

    def save_snapshot(self, node: str, blob: bytes, meta: dict, primary_table: str = "") -> None:
        primary_table = primary_table or self.primary_table
        slot = self._snapshot_slot(node)
        slot.archive_latest()
        empty = snapshot_is_empty(meta, primary_table)
        info = {**meta, "stored_at": datetime.now(tz=UTC).isoformat(), "empty": empty}
        raw = _meta_json(info)
        atomic_write_bytes(slot.latest, blob)
        atomic_write_bytes(slot.latest_meta, raw)
        if not empty:
            atomic_write_bytes(slot.directory / SNAPSHOT_NONEMPTY_FILE, blob)
            atomic_write_bytes(slot.directory / SNAPSHOT_NONEMPTY_META_FILE, raw)
        log.info(
            "Бэкап снапшота БД ноды %s сохранён (ревизия %s, строк %s)",
            node,
            meta.get("rev"),
            (meta.get("rows") or {}).get(primary_table),
        )

    def load_snapshot(self, node: str) -> StoredSnapshot | None:
        return self._load_snapshot_file(node, SNAPSHOT_FILE, SNAPSHOT_META_FILE)

    def load_last_nonempty(self, node: str) -> StoredSnapshot | None:
        return self._load_snapshot_file(node, SNAPSHOT_NONEMPTY_FILE, SNAPSHOT_NONEMPTY_META_FILE)

    def _load_snapshot_file(
        self, node: str, blob_file: str, meta_file: str
    ) -> StoredSnapshot | None:
        directory = self.snapshots_dir(node)
        if not (directory / blob_file).exists():
            return None
        return StoredSnapshot(
            (directory / blob_file).read_bytes(), _read_meta(directory / meta_file)
        )

    def snapshot_history(self, node: str) -> list[Path]:
        return self._snapshot_slot(node).history()

    def identity_versions(self, node: str) -> list[dict]:
        slot = self._identity_slot(node)
        versions = []
        if slot.latest.exists():
            versions.append({"label": "latest", "meta": _read_meta(slot.latest_meta)})
        return versions + slot.history_versions()

    def snapshot_versions(self, node: str) -> list[dict]:
        slot = self._snapshot_slot(node)
        versions = []
        if slot.latest.exists():
            versions.append({"label": "latest", "meta": _read_meta(slot.latest_meta)})
        if (slot.directory / SNAPSHOT_NONEMPTY_FILE).exists():
            versions.append(
                {
                    "label": "last_nonempty",
                    "meta": _read_meta(slot.directory / SNAPSHOT_NONEMPTY_META_FILE),
                }
            )
        return versions + slot.history_versions()

    def read_identity(self, node: str, label: str = "latest") -> StoredIdentity | None:
        if label == "latest":
            return self.load_identity(node)
        found = self._identity_slot(node).read_history(label)
        return StoredIdentity(*found) if found else None

    def read_snapshot(self, node: str, label: str = "latest") -> StoredSnapshot | None:
        if label == "latest":
            return self.load_snapshot(node)
        if label == "last_nonempty":
            return self.load_last_nonempty(node)
        found = self._snapshot_slot(node).read_history(label)
        return StoredSnapshot(*found) if found else None
