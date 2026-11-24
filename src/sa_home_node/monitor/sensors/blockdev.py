from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass

import psutil

log = logging.getLogger(__name__)

KIND_HDD = "hdd"
KIND_SSD = "ssd"
KIND_NVME = "nvme"
KIND_EMMC = "emmc"

KIND_LABEL = {KIND_HDD: "HDD", KIND_SSD: "SSD", KIND_NVME: "NVMe", KIND_EMMC: "eMMC"}

NVME_NAMESPACE_RE = re.compile(r"^(/dev/nvme\d+)n\d+$")


@dataclass(frozen=True)
class BlockDisk:
    path: str
    kind: str
    mountpoints: tuple[str, ...]
    model: str | None


def nvme_controller_path(path: str) -> str | None:
    m = NVME_NAMESPACE_RE.match(path)
    return m.group(1) if m else None


def is_rotational(rota) -> bool:
    if isinstance(rota, str):
        return rota.strip() != "0"
    if rota is None:
        return True
    return bool(rota)


def disk_kind(name: str, tran: str | None, rota) -> str:
    if tran == "mmc" or name.startswith("mmcblk"):
        return KIND_EMMC
    if tran == "nvme" or name.startswith("nvme"):
        return KIND_NVME
    if not is_rotational(rota):
        return KIND_SSD
    return KIND_HDD


def is_empty_media(size) -> bool:
    if size is None:
        return False
    if isinstance(size, str):
        size = size.strip()
        if not size:
            return False
    try:
        return int(size) == 0
    except (TypeError, ValueError):
        return False


def collect_mountpoints(node: dict, out: list[str]) -> None:
    mp = node.get("mountpoint")
    if not mp and isinstance(node.get("mountpoints"), list):
        mp = next((m for m in node["mountpoints"] if m), None)
    if mp and mp != "[SWAP]":
        out.append(mp)
    for child in node.get("children", []):
        collect_mountpoints(child, out)


def parse_lsblk_disks(data: dict) -> list[BlockDisk]:
    disks: list[BlockDisk] = []
    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk" or "boot" in (dev.get("name") or ""):
            continue
        if is_empty_media(dev.get("size")):
            continue
        mps: list[str] = []
        collect_mountpoints(dev, mps)
        name = dev.get("name") or ""
        disks.append(
            BlockDisk(
                path=dev.get("path") or f"/dev/{name}",
                kind=disk_kind(name, dev.get("tran"), dev.get("rota")),
                mountpoints=tuple(mps),
                model=dev.get("model"),
            )
        )
    return disks


def disk_usage(mountpoints: tuple[str, ...]) -> tuple[int | None, int | None]:
    free = total = 0
    seen = False
    for mp in mountpoints:
        try:
            u = psutil.disk_usage(mp)
        except OSError:
            continue
        free += u.free
        total += u.total
        seen = True
    return (free, total) if seen else (None, None)


def list_block_disks_sync() -> list[BlockDisk]:
    if shutil.which("lsblk") is None:
        return []
    try:
        out = subprocess.run(
            ["lsblk", "-J", "-b", "-o", "NAME,TYPE,MOUNTPOINT,TRAN,MODEL,PATH,ROTA,SIZE"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("lsblk не сработал: %s", exc)
        return []
    try:
        return parse_lsblk_disks(json.loads(out.stdout))
    except json.JSONDecodeError as exc:
        log.warning("lsblk: невалидный JSON: %s", exc)
        return []


def label_disks(disks: list[BlockDisk]) -> list[tuple[str, BlockDisk]]:
    ordered = sorted(disks, key=lambda x: (x.kind != KIND_EMMC, x.path))
    per_kind: dict[str, int] = {}
    for d in ordered:
        per_kind[d.kind] = per_kind.get(d.kind, 0) + 1
    counters: dict[str, int] = {}
    labelled: list[tuple[str, BlockDisk]] = []
    for d in ordered:
        base = KIND_LABEL[d.kind]
        if per_kind[d.kind] > 1:
            counters[d.kind] = counters.get(d.kind, 0) + 1
            label = f"{base}{counters[d.kind]}"
        else:
            label = base
        labelled.append((label, d))
    return labelled
