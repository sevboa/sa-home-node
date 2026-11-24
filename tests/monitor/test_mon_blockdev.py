from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from sa_home_node.monitor.sensors import blockdev
from sa_home_node.monitor.sensors.blockdev import (
    BlockDisk,
    disk_kind,
    disk_usage,
    label_disks,
    list_block_disks_sync,
    nvme_controller_path,
    parse_lsblk_disks,
)

LSBLK = {
    "blockdevices": [
        {
            "name": "mmcblk0",
            "type": "disk",
            "tran": None,
            "model": None,
            "path": "/dev/mmcblk0",
            "rota": "0",
            "size": "62537326592",
            "children": [
                {"name": "mmcblk0p1", "type": "part", "mountpoint": "/boot/efi"},
                {"name": "mmcblk0p2", "type": "part", "mountpoints": [None, "/"]},
                {"name": "mmcblk0p3", "type": "part", "mountpoint": "[SWAP]"},
            ],
        },
        {"name": "mmcblk0boot0", "type": "disk", "path": "/dev/mmcblk0boot0", "size": "4194304"},
        {"name": "sda", "type": "disk", "tran": "usb", "model": "Disk-A", "path": "/dev/sda", "rota": True, "size": "100"},
        {"name": "sdb", "type": "disk", "tran": "usb", "model": "Disk-B", "rota": "1", "size": "100"},
        {"name": "sdc", "type": "disk", "tran": "usb", "path": "/dev/sdc", "rota": "1", "size": "0"},
        {"name": "sr0", "type": "rom", "path": "/dev/sr0"},
        {"name": "nvme0n1", "type": "disk", "tran": "nvme", "path": "/dev/nvme0n1", "rota": "0", "size": "10"},
    ]
}


def test_parse_lsblk_disks():
    disks = parse_lsblk_disks(LSBLK)
    assert [(d.path, d.kind, d.mountpoints, d.model) for d in disks] == [
        ("/dev/mmcblk0", "emmc", ("/boot/efi", "/"), None),
        ("/dev/sda", "hdd", (), "Disk-A"),
        ("/dev/sdb", "hdd", (), "Disk-B"),
        ("/dev/nvme0n1", "nvme", (), None),
    ]


def test_parse_lsblk_old_format_without_columns():
    data = {"blockdevices": [{"name": "sda", "type": "disk"}, {"name": "sdb", "type": "disk", "mountpoint": "/data"}]}
    disks = parse_lsblk_disks(data)
    assert [(d.path, d.kind, d.mountpoints) for d in disks] == [
        ("/dev/sda", "hdd", ()),
        ("/dev/sdb", "hdd", ("/data",)),
    ]
    assert parse_lsblk_disks({}) == []


@pytest.mark.parametrize(
    ("path", "expected"),
    [("/dev/nvme0n1", "/dev/nvme0"), ("/dev/nvme12n3", "/dev/nvme12"), ("/dev/nvme0", None), ("/dev/sda", None)],
)
def test_nvme_controller_path(path, expected):
    assert nvme_controller_path(path) == expected


@pytest.mark.parametrize(
    ("name", "tran", "rota", "expected"),
    [
        ("sda", "mmc", "1", "emmc"),
        ("mmcblk1", None, "1", "emmc"),
        ("nvme0n1", None, "1", "nvme"),
        ("sda", "nvme", None, "nvme"),
        ("sda", "sata", "0", "ssd"),
        ("sda", "sata", False, "ssd"),
        ("sda", "sata", " 1 ", "hdd"),
        ("sda", "sata", None, "hdd"),
    ],
)
def test_disk_kind(name, tran, rota, expected):
    assert disk_kind(name, tran, rota) == expected


def _d(path, kind):
    return BlockDisk(path, kind, (), None)


def test_label_disks():
    labelled = label_disks(
        [_d("/dev/sdb", "hdd"), _d("/dev/nvme0n1", "nvme"), _d("/dev/sda", "hdd"), _d("/dev/mmcblk0", "emmc"), _d("/dev/sdc", "ssd")]
    )
    assert [(label, d.path) for label, d in labelled] == [
        ("eMMC", "/dev/mmcblk0"),
        ("NVMe", "/dev/nvme0n1"),
        ("HDD1", "/dev/sda"),
        ("HDD2", "/dev/sdb"),
        ("SSD", "/dev/sdc"),
    ]


def test_disk_usage(monkeypatch):
    def fake(mp):
        if mp == "/bad":
            raise OSError
        return SimpleNamespace(free=10, total=40)

    monkeypatch.setattr(blockdev.psutil, "disk_usage", fake)
    assert disk_usage(("/a", "/bad", "/b")) == (20, 80)
    assert disk_usage(("/bad",)) == (None, None)
    assert disk_usage(()) == (None, None)


def test_list_block_disks_sync(monkeypatch):
    import json

    monkeypatch.setattr(blockdev.shutil, "which", lambda n: "/usr/bin/lsblk")
    monkeypatch.setattr(
        blockdev.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=json.dumps(LSBLK))
    )
    assert len(list_block_disks_sync()) == 4

    monkeypatch.setattr(blockdev.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="{"))
    assert list_block_disks_sync() == []

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("lsblk", 10)

    monkeypatch.setattr(blockdev.subprocess, "run", boom)
    assert list_block_disks_sync() == []

    monkeypatch.setattr(blockdev.shutil, "which", lambda n: None)
    assert list_block_disks_sync() == []
