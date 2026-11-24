from __future__ import annotations

import os
from datetime import datetime

from sa_home_node.monitor.models import DiskSummary, SensorReading, SmartSnapshot
from sa_home_node.monitor.sensors import blockdev, smartctl
from sa_home_node.monitor.smartdiff import MONITORED_SMART_ATTRS


def parse_device_temp(device: str, data: dict, now: datetime) -> SensorReading | None:
    temp = smartctl.extract_temp(data)
    if temp is None:
        return None
    model = data.get("model_name") or data.get("device", {}).get("name") or device
    return SensorReading(f"disk:{device}", "disk", model, temp, now)


def read_disks_sync(specs: list[str], now: datetime) -> list[SensorReading]:
    readings: list[SensorReading] = []
    seen: set[str] = set()
    for target in smartctl.resolve_targets(specs):
        if not smartctl.is_smart_capable(target.name):
            continue
        real = os.path.realpath(target.name)
        if real in seen:
            continue
        seen.add(real)
        data = smartctl.run_smartctl(smartctl.read_args(target))
        if not data:
            continue
        reading = parse_device_temp(target.name, data, now)
        if reading is not None:
            readings.append(reading)
    return readings


def _raw_count(raw: dict) -> int | None:
    s = raw.get("string")
    if isinstance(s, str):
        head = s.strip().split(" ", 1)[0]
        try:
            return int(head)
        except ValueError:
            pass
    v = raw.get("value")
    return int(v) if v is not None else None


def _raw_attrs(data: dict) -> dict[int, int]:
    out: dict[int, int] = {}
    for a in data.get("ata_smart_attributes", {}).get("table", []):
        aid = a.get("id")
        if aid in MONITORED_SMART_ATTRS:
            val = _raw_count(a.get("raw", {}))
            if val is not None:
                out[aid] = val
    return out


def parse_smart_snapshot(component_id: str, data: dict, now: datetime) -> SmartSnapshot:
    model = data.get("model_name") or data.get("device", {}).get("name") or component_id
    return SmartSnapshot(component_id, str(model), smartctl.parse_health(data), _raw_attrs(data), now)


def read_smart_snapshots_sync(specs: list[str], now: datetime) -> list[SmartSnapshot]:
    snapshots: list[SmartSnapshot] = []
    seen: set[str] = set()
    for target in smartctl.resolve_targets(specs):
        if not smartctl.is_smart_capable(target.name):
            continue
        real = os.path.realpath(target.name)
        if real in seen:
            continue
        seen.add(real)
        data = smartctl.run_smartctl(smartctl.health_args(target))
        if not data:
            continue
        snapshots.append(parse_smart_snapshot(f"disk:{real}", data, now))
    return snapshots


def read_disk_summaries_sync(
    specs: list[str], health_overrides: dict[str, str | None] | None = None
) -> list[DiskSummary]:
    smart: dict[str, tuple[str | None, float | None]] = {}
    for target in smartctl.resolve_targets(specs):
        if not smartctl.is_smart_capable(target.name):
            continue
        data = smartctl.run_smartctl(smartctl.health_args(target))
        if not data:
            continue
        real = os.path.realpath(target.name)
        smart[real] = (smartctl.parse_health(data), smartctl.extract_temp(data))

    summaries: list[DiskSummary] = []
    for label, disk in blockdev.label_disks(blockdev.list_block_disks_sync()):
        real = os.path.realpath(disk.path)
        if disk.kind == blockdev.KIND_NVME:
            controller = blockdev.nvme_controller_path(disk.path)
            if controller is not None:
                real = os.path.realpath(controller)
        live_health, temp = smart.get(real, (None, None))
        if health_overrides is not None and real in health_overrides:
            live_health = health_overrides[real]
        free, total = blockdev.disk_usage(disk.mountpoints)
        summaries.append(
            DiskSummary(
                label=label,
                health=live_health,
                temperature_c=temp,
                free_bytes=free,
                total_bytes=total,
                model=disk.model,
                kind=disk.kind,
            )
        )
    return summaries
