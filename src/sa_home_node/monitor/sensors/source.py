from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TypeVar

from sa_home_node import host
from sa_home_node.monitor.config import SensorsConfig
from sa_home_node.monitor.hostmodels import HostMetricReading
from sa_home_node.monitor.models import DiskSummary, SensorReading, SmartSnapshot
from sa_home_node.monitor.providers import SensorProvider
from sa_home_node.monitor.sensors import cpu, disks, gpu, hostscan
from sa_home_node.services.slot import utc_now

T = TypeVar("T")


async def _in_executor(func: Callable[..., T], *args: object) -> T:
    return await asyncio.get_running_loop().run_in_executor(None, func, *args)


class SensorSource:
    def __init__(self, config: SensorsConfig, providers: Sequence[SensorProvider] = ()) -> None:
        self._config = config
        self._providers = list(providers)

    def _provider(self) -> SensorProvider | None:
        name = host.current().name
        return next((p for p in self._providers if p.platform == name), None)

    def problems(self) -> list[dict]:
        return [p for provider in self._providers if (p := provider.problem()) is not None]

    async def read_cpu(self) -> list[SensorReading]:
        if not self._config.cpu.enabled:
            return []
        provider = self._provider()
        if provider is not None:
            return await _in_executor(provider.read_cpu, utc_now())
        if host.current().name == "windows":
            return []
        return await _in_executor(cpu.read_cpu_sync, utc_now())

    async def read_gpu(self) -> list[SensorReading]:
        if not self._config.gpu.enabled:
            return []
        return await _in_executor(gpu.read_gpu_sync, utc_now())

    async def read_disks(self) -> list[SensorReading]:
        if not self._config.disks.enabled:
            return []
        provider = self._provider()
        if provider is not None:
            return await _in_executor(provider.read_disks, utc_now())
        if host.current().name == "windows":
            return []
        return await _in_executor(disks.read_disks_sync, list(self._config.disks.devices), utc_now())

    async def read_host(
        self, kernel_since: datetime | None = None, export: bool = False
    ) -> list[HostMetricReading]:
        if not (self._config.host.enabled or export):
            return []
        return await _in_executor(
            hostscan.read_host_sync, utc_now(), self._config.host.sample_window_s, kernel_since
        )

    async def read_all(self) -> list[SensorReading]:
        cpu_readings, gpu_readings, disk_readings = await asyncio.gather(
            self.read_cpu(), self.read_gpu(), self.read_disks()
        )
        return [*cpu_readings, *gpu_readings, *disk_readings]

    async def read_smart_snapshots(self) -> list[SmartSnapshot]:
        if not self._config.disks.enabled:
            return []
        return await _in_executor(
            disks.read_smart_snapshots_sync, list(self._config.disks.devices), utc_now()
        )

    async def read_disk_summaries(self, health_overrides: dict[str, str | None]) -> list[DiskSummary]:
        provider = self._provider()
        if provider is not None:
            return await _in_executor(provider.read_disk_summaries)
        if host.current().name == "windows":
            return []
        return await _in_executor(
            disks.read_disk_summaries_sync, list(self._config.disks.devices), health_overrides
        )
