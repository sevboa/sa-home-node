from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sa_home_node.monitor.models import DiskSummary, SensorReading, SmartSnapshot
from sa_home_node.monitor.sensors import blockdev, disks, smartctl

FIXTURE = Path(__file__).parents[1] / "fixtures" / "monitor" / "sensors"
IN = FIXTURE / "in"
NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def load_in(name):
    return json.loads((IN / f"smartctl-{name}.json").read_text())


SCAN = load_in("scan")
ATA_OK = load_in("ata-ok")
ATA_PENDING = load_in("ata-pending")
NVME = load_in("nvme")
NO_STATUS = load_in("no-status")
ATA_FAILED_NO_DEVICE = {**load_in("ata-failed")}
ATA_FAILED_NO_DEVICE.pop("device")

SMART_BY_DISK = {
    "/dev/sda": ATA_OK,
    "/dev/sdb": ATA_PENDING,
    "/dev/nvme0": NVME,
}

HOST_DISKS = [
    blockdev.BlockDisk("/dev/mmcblk0", "emmc", ("/boot/efi", "/"), None),
    blockdev.BlockDisk("/dev/nvme0n1", "nvme", ("/mnt/nvme",), "Disk-C"),
    blockdev.BlockDisk("/dev/sda", "hdd", ("/mnt/data",), "Disk-A"),
    blockdev.BlockDisk("/dev/sdb", "hdd", ("/mnt/scratch",), "Disk-B"),
]

USAGE = {
    "/boot/efi": (1000, 1000),
    "/": (29480, 59512),
    "/mnt/data": (100000, 300000),
    "/mnt/scratch": (7, 10),
}


def fake_usage(mountpoints):
    pairs = [USAGE.get(mp) for mp in mountpoints]
    if not mountpoints or None in pairs or not all(pairs):
        return (None, None)
    return (sum(f for f, _ in pairs), sum(t for _, t in pairs))


def fake_smart(by_disk, scan):
    def fake(args):
        if "--scan" in args:
            return scan
        return by_disk.get(args[-1])

    return fake


def parse_json():
    return json.loads((FIXTURE / "smartctl-parse.json").read_text())


def parse_disk_summaries():
    return json.loads((FIXTURE / "disk-summaries.json").read_text())


def as_reading(e):
    return SensorReading(
        e["component_id"], e["kind"], e["label"], e["temperature_c"], datetime.fromisoformat(e["taken_at"])
    )


def as_snapshot(e):
    return SmartSnapshot(
        e["component_id"],
        e["label"],
        e["health"],
        {int(k): v for k, v in e["attrs"].items()},
        datetime.fromisoformat(e["taken_at"]),
    )


def as_summary(e):
    return DiskSummary(
        label=e["label"],
        health=e["health"],
        temperature_c=e["temperature_c"],
        free_bytes=e["free_bytes"],
        total_bytes=e["total_bytes"],
        model=e["model"],
        kind=e["kind"],
    )


def test_parse_device_temp_scenarios():
    d = parse_json()
    for name, e in d["parse_device_temp"].items():
        data = load_in(name.replace("smartctl-", "").replace(".json", ""))
        device = data.get("device", {}).get("name") or "/dev/sdx"
        assert (disks.parse_device_temp(device, data, NOW) is None) == (e is None)
        if e is not None:
            assert disks.parse_device_temp(device, data, NOW) == as_reading(e)


def test_parse_smart_snapshot_scenarios():
    d = parse_json()
    for name, e in d["parse_smart_snapshot"].items():
        data = load_in(name.replace("smartctl-", "").replace(".json", ""))
        device = data.get("device", {}).get("name") or "/dev/sdx"
        assert disks.parse_smart_snapshot(f"disk:{device}", data, NOW) == as_snapshot(e)


def test_read_disks(monkeypatch):
    monkeypatch.setattr(smartctl, "run_smartctl", fake_smart(SMART_BY_DISK, SCAN))
    d = parse_json()["read_disks"]
    assert disks.read_disks_sync([], NOW) == [as_reading(e) for e in d["discover"]]
    assert disks.read_disks_sync(["/dev/sda:sat", "/dev/sdb"], NOW) == [as_reading(e) for e in d["specs"]]


def test_read_smart_snapshots(monkeypatch):
    monkeypatch.setattr(smartctl, "run_smartctl", fake_smart(SMART_BY_DISK, SCAN))
    d = parse_json()["read_smart_snapshots"]
    assert disks.read_smart_snapshots_sync([], NOW) == [as_snapshot(e) for e in d["discover"]]
    assert disks.read_smart_snapshots_sync(["/dev/sda", "/dev/sda:sat", "/dev/nvme0"], NOW) == [
        as_snapshot(e) for e in d["duplicates"]
    ]


def test_read_disk_summaries(monkeypatch):
    monkeypatch.setattr(smartctl, "run_smartctl", fake_smart(SMART_BY_DISK, SCAN))
    monkeypatch.setattr(blockdev, "list_block_disks_sync", lambda: HOST_DISKS)
    monkeypatch.setattr(blockdev, "disk_usage", fake_usage)
    d = parse_disk_summaries()
    assert disks.read_disk_summaries_sync([]) == [as_summary(e) for e in d["discover"]]
    assert disks.read_disk_summaries_sync(["/dev/sda"]) == [as_summary(e) for e in d["specs_only_sda"]]
    assert disks.read_disk_summaries_sync(
        [], {"/dev/sda": "ok", "/dev/sdb": None, "/dev/nvme0": "failed"}
    ) == [as_summary(e) for e in d["health_overrides"]]
    assert disks.read_disk_summaries_sync([], {}) == [as_summary(e) for e in d["health_overrides_empty"]]
