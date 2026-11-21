from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sa_home_node.files import atomic_write_bytes, atomic_write_text
from sa_home_node.services.assignments import slot_key, valid_instance, valid_service

log = logging.getLogger(__name__)

PACKAGE_SUFFIX = ".toml"
META_SUFFIX = ".rev.json"
INSTANCES_DIRNAME = "instances"


@dataclass(frozen=True)
class InstanceMeta:
    service: str
    instance: str
    rev: int
    hash: str
    updated_at: str
    origin_node: str

    def __post_init__(self) -> None:
        _stem(self.service, self.instance)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> InstanceMeta:
        try:
            return cls(
                service=str(data.get("service", "")),
                instance=str(data.get("instance", "")),
                rev=int(data.get("rev", 0)),
                hash=str(data.get("hash", "")),
                updated_at=str(data.get("updated_at", "")),
                origin_node=str(data.get("origin_node", "")),
            )
        except (AttributeError, TypeError, OverflowError) as exc:
            raise ValueError(f"битая запись пакета: {exc}") from exc

    def wins_over(self, other: InstanceMeta | None) -> bool:
        if other is None:
            return True
        if self.rev != other.rev:
            return self.rev > other.rev
        if self.hash == other.hash:
            return False
        return (self.updated_at, self.origin_node) > (other.updated_at, other.origin_node)


def content_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _split(path: Path) -> tuple[str, str]:
    service, _, instance = path.name[: -len(PACKAGE_SUFFIX)].partition(".")
    return service, instance


def _stem(service: str, instance: str) -> str:
    if not valid_service(service) or not valid_instance(instance):
        raise ValueError(f"недопустимое имя пакета: {slot_key(service, instance)!r}")
    return f"{service}.{instance}"


def _named_ok(path: Path, service: str, instance: str) -> bool:
    if valid_service(service) and valid_instance(instance):
        return True
    log.warning("Пакет %s: недопустимое имя — пропускаю", path.name)
    return False


class InstanceStore:
    def __init__(self, directory: str | Path, node_id: str) -> None:
        self.dir = Path(directory)
        self._node_id = node_id

    def package_path(self, service: str, instance: str) -> Path:
        return self.dir / f"{_stem(service, instance)}{PACKAGE_SUFFIX}"

    def meta_path(self, service: str, instance: str) -> Path:
        return self.dir / f"{_stem(service, instance)}{META_SUFFIX}"

    def read_package(self, service: str, instance: str) -> bytes | None:
        path = self.package_path(service, instance)
        return path.read_bytes() if path.exists() else None

    def read_meta(self, service: str, instance: str) -> InstanceMeta | None:
        path = self.meta_path(service, instance)
        if not path.exists():
            return None
        try:
            return InstanceMeta.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            log.warning(
                "Пакет %s: повреждён сайдкар ревизии (%s) — считаю его отсутствующим",
                slot_key(service, instance),
                exc,
            )
            return None

    def known(self) -> list[InstanceMeta]:
        metas = []
        for path in self._packages():
            service, instance = _split(path)
            if not instance:
                log.warning("Пакет %s: имя не вида '<служба>.<инстанс>.toml' — пропускаю", path.name)
                continue
            if not _named_ok(path, service, instance):
                continue
            meta = self.read_meta(service, instance)
            if meta is not None:
                metas.append(meta)
        return metas

    def refresh(self, service: str, instance: str) -> InstanceMeta | None:
        data = self.read_package(service, instance)
        if data is None:
            return None
        current = content_hash(data)
        meta = self.read_meta(service, instance)
        if meta is not None and meta.hash == current:
            return None
        new_meta = InstanceMeta(
            service=service,
            instance=instance,
            rev=meta.rev + 1 if meta is not None else 1,
            hash=current,
            updated_at=datetime.now(tz=UTC).isoformat(),
            origin_node=self._node_id,
        )
        self._write_meta(new_meta)
        log.info("Пакет %s изменён локально — ревизия %d", slot_key(service, instance), new_meta.rev)
        return new_meta

    def refresh_all(self) -> list[InstanceMeta]:
        changed = []
        for path in self._packages():
            service, instance = _split(path)
            if not instance or not _named_ok(path, service, instance):
                continue
            meta = self.refresh(service, instance)
            if meta is not None:
                changed.append(meta)
        return changed

    def apply(self, meta: InstanceMeta, data: bytes, *, force: bool = False) -> bool:
        key = slot_key(meta.service, meta.instance)
        actual = content_hash(data)
        if actual != meta.hash:
            log.warning(
                "Пакет %s: хеш содержимого (%s) не сходится с заявленным (%s) — отвергаю",
                key,
                actual,
                meta.hash,
            )
            return False
        local = self.read_meta(meta.service, meta.instance)
        if not force and not meta.wins_over(local):
            return False
        if local is not None and local.rev == meta.rev and local.hash != meta.hash:
            log.warning(
                "Пакет %s: расхождение на одной ревизии %d (правили в двух местах) — "
                "принимаю версию ноды %s от %s",
                key,
                meta.rev,
                meta.origin_node,
                meta.updated_at,
            )
        atomic_write_bytes(self.package_path(meta.service, meta.instance), data)
        self._write_meta(meta)
        log.info("Пакет %s обновлён с ноды %s — ревизия %d", key, meta.origin_node, meta.rev)
        return True

    def _packages(self) -> list[Path]:
        if not self.dir.exists():
            return []
        return sorted(self.dir.glob(f"*{PACKAGE_SUFFIX}"))

    def _write_meta(self, meta: InstanceMeta) -> None:
        text = json.dumps(meta.to_dict(), ensure_ascii=False, indent=2)
        atomic_write_text(self.meta_path(meta.service, meta.instance), text)
