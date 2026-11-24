from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass

from sa_home_node.monitor.models import DISK_FAIL, DISK_OK, DISK_WARN
from sa_home_node.requirements import (
    Requirement,
    RequirementStatus,
    looks_like_permission_error,
    requirements_registry,
)

log = logging.getLogger(__name__)

SMARTCTL_TIMEOUT_S = 20

SMARTCTL_REQUIREMENT = Requirement(
    program="smartctl",
    package="smartmontools",
    note="температура и здоровье дисков по SMART",
)

UNSUPPORTED_PREFIXES = (
    "/dev/mmcblk",
    "/dev/loop",
    "/dev/sr",
    "/dev/md",
    "/dev/dm-",
    "/dev/zram",
    "/dev/ram",
)


@dataclass(frozen=True)
class DiskTarget:
    name: str
    dev_type: str | None = None


def is_smart_capable(name: str) -> bool:
    return not name.startswith(UNSUPPORTED_PREFIXES)


def parse_device_spec(spec: str) -> DiskTarget | None:
    spec = spec.strip()
    if not spec:
        return None
    name, sep, dev_type = spec.rpartition(":")
    if not sep:
        return DiskTarget(spec, None)
    name = name.strip()
    if not name:
        return None
    return DiskTarget(name, dev_type.strip() or None)


def parse_scan(data: dict) -> list[DiskTarget]:
    targets: list[DiskTarget] = []
    for device in data.get("devices", []):
        name = device.get("name")
        if not name:
            continue
        targets.append(DiskTarget(name, device.get("type") or None))
    return targets


def _target_args(target: DiskTarget, flags: list[str]) -> list[str]:
    args = ["-d", target.dev_type] if target.dev_type else []
    return [*args, *flags, target.name]


def read_args(target: DiskTarget) -> list[str]:
    return _target_args(target, ["-j", "-A"])


def health_args(target: DiskTarget) -> list[str]:
    return _target_args(target, ["-j", "-H", "-A"])


def extract_temp(data: dict) -> float | None:
    temp = None
    if isinstance(data.get("temperature"), dict):
        temp = data["temperature"].get("current")
    if temp is None:
        for attr in data.get("ata_smart_attributes", {}).get("table", []):
            if attr.get("id") == 194:
                temp = attr.get("raw", {}).get("value")
                break
    return float(temp) if temp is not None else None


def _raw_attr(data: dict, attr_id: int) -> int:
    for attr in data.get("ata_smart_attributes", {}).get("table", []):
        if attr.get("id") == attr_id:
            return attr.get("raw", {}).get("value") or 0
    return 0


def parse_health(data: dict) -> str | None:
    passed = data.get("smart_status", {}).get("passed")
    if passed is None:
        return None
    if passed is False:
        return DISK_FAIL
    if _raw_attr(data, 197) or _raw_attr(data, 198):
        return DISK_WARN
    return DISK_OK


def run_smartctl(args: list[str]) -> dict | None:
    diagnosis = SMARTCTL_REQUIREMENT.diagnose()
    if diagnosis is not RequirementStatus.OK:
        requirements_registry.report(SMARTCTL_REQUIREMENT, diagnosis)
        log.warning("smartctl недоступен: %s", SMARTCTL_REQUIREMENT.install_hint())
        return None
    command = " ".join(args)
    try:
        out = subprocess.run(
            ["smartctl", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=SMARTCTL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        log.warning("smartctl %s: таймаут %dс", command, SMARTCTL_TIMEOUT_S)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("smartctl %s не сработал: %s", command, exc)
        return None
    if not out.stdout.strip():
        if looks_like_permission_error(out.stderr):
            requirements_registry.report(SMARTCTL_REQUIREMENT, RequirementStatus.NEEDS_PRIVILEGE)
        return None
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        log.warning("smartctl %s: невалидный JSON: %s", command, exc)
        return None
    requirements_registry.report(SMARTCTL_REQUIREMENT, RequirementStatus.OK)
    return data


def discover_devices_sync() -> list[DiskTarget]:
    data = run_smartctl(["--scan", "-j"])
    if not data:
        return []
    return [t for t in parse_scan(data) if is_smart_capable(t.name)]


def resolve_targets(specs: list[str]) -> list[DiskTarget]:
    if not specs:
        return discover_devices_sync()
    targets: list[DiskTarget] = []
    for spec in specs:
        target = parse_device_spec(spec)
        if target is None:
            log.warning("disks.devices: некорректная запись %r — пропущена", spec)
            continue
        targets.append(target)
    return targets
