from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sa_home_node.backup.config import BackupConfig
from sa_home_node.config import NodeConfig


class IdentityError(Exception):
    pass


class PrivilegedIO(Protocol):
    def read_root(self, path: Path) -> str | None: ...
    def write_root(self, path: Path, text: str) -> None: ...
    def unit_active(self, unit: str, *, user: bool = False) -> bool: ...
    def restart(self, unit: str, *, user: bool = False) -> None: ...


@dataclass(frozen=True)
class BackupContext:
    config: NodeConfig
    backup: BackupConfig
    node_id: str


@dataclass(frozen=True)
class ApplyContext:
    bundle: dict[str, Any]
    config: NodeConfig
    config_path: Path
    io: PrivilegedIO
    log: Callable[[str], None]
    wipe_db: bool


@dataclass(frozen=True)
class BackupSpec:
    service: str
    identity_service: str
    identity_format: str
    snapshot_format: str
    bundle_format: str
    tables: tuple[str, ...]
    skip_tables: frozenset[str]
    primary_table: str
    trigger_actions: frozenset[str]
    collect_identity: Callable[[BackupContext], Awaitable[dict[str, Any]]]
    apply: Callable[[ApplyContext], Awaitable[None]]
    summarize: Callable[[dict[str, Any]], list[str]]
    rows_extra: Callable[[dict[str, Any]], dict[str, int]]
