from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sa_home_node.monitor.models import DiskSummary, SensorReading


class SensorProvider(Protocol):
    platform: str

    def read_cpu(self, now: datetime) -> list[SensorReading]: ...

    def read_disks(self, now: datetime) -> list[SensorReading]: ...

    def read_disk_summaries(self) -> list[DiskSummary]: ...

    def problem(self) -> dict | None: ...
