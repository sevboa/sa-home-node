from __future__ import annotations

import asyncio
import socket
from collections.abc import Sequence
from typing import Any

from sa_home_node.monitor.config import SensorsConfig
from sa_home_node.monitor.jobbase import DedupQueue
from sa_home_node.monitor.metrics import METRICS
from sa_home_node.monitor.payloads import health_dict, host_state_dict, outage_dict
from sa_home_node.monitor.providers import SensorProvider
from sa_home_node.monitor.scan import SensorScanJob
from sa_home_node.monitor.sensors.gpu import NVIDIA_SMI_REQUIREMENT
from sa_home_node.monitor.sensors.hostscan import KERNEL_JOURNAL_REQUIREMENT
from sa_home_node.monitor.sensors.power import (
    JOURNALCTL_REQUIREMENT,
    LAST_REQUIREMENT,
    read_power_events_sync,
    read_uptime_sync,
)
from sa_home_node.monitor.sensors.smartctl import SMARTCTL_REQUIREMENT
from sa_home_node.monitor.smartscan import SmartScanJob
from sa_home_node.monitor.store import MonitorStore
from sa_home_node.proto.describe import ActionParam, ActionSpec, ServiceDescription, ServiceInfo
from sa_home_node.proto.wire import ERR_UNKNOWN_ACTION, ProtoError
from sa_home_node.requirements import requirements_registry
from sa_home_node.services.slot import utc_now

SERVICE_NAME = "monitor"

ACTION_SCAN_NOW = "scan_now"
ACTION_DOWNTIME = "downtime"
ACTION_HOST_TREND = "host_trend"

HOST_TREND_DEFAULT_HOURS = 24
HOST_TREND_MAX_HOURS = 24 * 14

DOWNTIME_DEFAULT_LIMIT = 10
DOWNTIME_MAX_LIMIT = 50


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class MonitorService:
    def __init__(
        self,
        sensors: SensorsConfig,
        store: MonitorStore,
        queue: DedupQueue,
        providers: Sequence[SensorProvider] = (),
        *,
        version: str,
    ) -> None:
        self.sensors = sensors
        self.store = store
        self.queue = queue
        self.providers = providers
        self._version = version
        self._node = socket.gethostname()

    def describe(self) -> ServiceDescription:
        return ServiceDescription(
            info=ServiceInfo(node=self._node, service=SERVICE_NAME, version=self._version),
            capabilities=("temperature", "smart", "power", "host"),
            actions=(
                ActionSpec(id=ACTION_SCAN_NOW, title="🔄 Скан датчиков"),
                ActionSpec(
                    id=ACTION_DOWNTIME,
                    title="⏻ Отключения",
                    params=(
                        ActionParam(name="offset", type="int", required=False),
                        ActionParam(name="limit", type="int", required=False),
                    ),
                ),
                ActionSpec(
                    id=ACTION_HOST_TREND,
                    title="📊 Тренд host-метрики",
                    params=(
                        ActionParam(name="metric", type="string", required=True),
                        ActionParam(name="hours", type="int", required=False),
                    ),
                ),
            ),
        )

    def _requirements(self) -> list[dict]:
        sensors = self.sensors
        problem = requirements_registry.problem_for
        last_problem = problem(LAST_REQUIREMENT)
        found = (
            problem(SMARTCTL_REQUIREMENT) if sensors.disks.enabled else None,
            problem(NVIDIA_SMI_REQUIREMENT) if sensors.gpu.enabled else None,
            last_problem,
            None if last_problem else problem(JOURNALCTL_REQUIREMENT),
            problem(KERNEL_JOURNAL_REQUIREMENT) if sensors.host.enabled else None,
            *(p.problem() for p in self.providers),
        )
        return [p for p in found if p is not None]

    async def get_state(self) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        sensors = self.sensors
        states = await self.store.get_all_states()
        host_states = await self.store.get_all_host_states()
        disks = await self.store.get_disk_summaries() or []
        uptime, (outages, _) = await asyncio.gather(
            loop.run_in_executor(None, read_uptime_sync),
            loop.run_in_executor(None, read_power_events_sync, 0, 1),
        )
        return {
            "node": self._node,
            "service": SERVICE_NAME,
            "now": utc_now().isoformat(),
            "uptime_s": uptime.total_seconds() if uptime is not None else None,
            "health": [health_dict(s) for s in states],
            "host_health": [host_state_dict(s) for s in host_states] if sensors.host.enabled else [],
            "disks": [vars(d) for d in disks],
            "last_outage": outage_dict(outages[0] if outages else None),
            "job_counts": await self.store.job_run_counts(),
            "recent_runs": await self.store.recent_job_runs(limit=8),
            "thresholds": {
                name: {"warn_c": cfg.warn_c, "crit_c": cfg.crit_c}
                for name, cfg in (("cpu", sensors.cpu), ("gpu", sensors.gpu), ("disk", sensors.disks))
            },
            "host_thresholds": self._host_thresholds(),
            "requirements": self._requirements(),
        }

    def _host_thresholds(self) -> dict[str, dict[str, Any]]:
        overrides = self.sensors.host.thresholds
        return {
            name: {
                "warn": ov.warn if (ov := overrides.get(name)) else spec.warn,
                "crit": ov.crit if ov else spec.crit,
                "direction": spec.direction,
            }
            for name, spec in METRICS.items()
        }

    async def run_command(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        if action == ACTION_SCAN_NOW:
            return {
                "sensor_queued": await self.queue.put(SensorScanJob()),
                "smart_queued": await self.queue.put(SmartScanJob()),
            }
        if action == ACTION_DOWNTIME:
            return await self._downtime_page(args)
        if action == ACTION_HOST_TREND:
            return await self._host_trend_page(args)
        raise ProtoError(ERR_UNKNOWN_ACTION, f"необъявленное действие: {action}")

    async def _host_trend_page(self, args: dict[str, Any]) -> dict[str, Any]:
        metric = str(args.get("metric", "")).strip()
        if metric not in METRICS:
            return {"error": "unknown_metric", "metric": metric, "known": sorted(METRICS)}
        hours = max(
            1,
            min(
                _int(args.get("hours", HOST_TREND_DEFAULT_HOURS), HOST_TREND_DEFAULT_HOURS),
                HOST_TREND_MAX_HOURS,
            ),
        )
        buckets = await self.store.host_trend(metric, hours, utc_now())
        return {"metric": metric, "hours": hours, "buckets": buckets}

    async def _downtime_page(self, args: dict[str, Any]) -> dict[str, Any]:
        offset = max(0, _int(args.get("offset", 0), 0))
        limit = max(
            1,
            min(_int(args.get("limit", DOWNTIME_DEFAULT_LIMIT), DOWNTIME_DEFAULT_LIMIT), DOWNTIME_MAX_LIMIT),
        )
        events, has_next = await asyncio.get_running_loop().run_in_executor(
            None, read_power_events_sync, offset, limit
        )
        return {"events": [outage_dict(e) for e in events], "offset": offset, "has_next": has_next}
