from __future__ import annotations

import grp
import logging
import os
import re
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from sa_home_node import netif
from sa_home_node.monitor.hostmodels import HostMetricReading
from sa_home_node.monitor.metrics import metric_spec
from sa_home_node.monitor.sensors import procfs
from sa_home_node.requirements import (
    Requirement,
    RequirementStatus,
    looks_like_permission_error,
    requirements_registry,
)

log = logging.getLogger(__name__)

KERNEL_SCAN_STATE_KEY = "host:kernel_scan_at"

KERNEL_JOURNAL_REQUIREMENT = Requirement(
    program="journalctl",
    package="systemd",
    platforms=("linux",),
    key="journalctl-kernel",
    note=(
        "счётчик стойл ядра/гипервизора (нужна группа systemd-journal; "
        "после nodectl fix нужен sudo systemctl restart user@<uid>.service)"
    ),
)

_JOURNAL_GROUPS = ("systemd-journal", "adm")

_STALL_PATTERN = re.compile(
    r"hung_task"
    r"|rcu_(?:sched|preempt)[^\n]*stall"
    r"|clocksource[^\n]*(?:unstable|Marking)"
    r"|soft lockup"
    r"|BUG: soft"
    r"|NETDEV WATCHDOG"
    r"|TX timeout"
    r"|blocked for more than \d+ seconds",
    re.IGNORECASE,
)

_REAL_FILESYSTEMS = frozenset(
    {"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "zfs", "ntfs", "ntfs3", "fuseblk", "exfat"}
)
_MOUNT_PREFIX = "/mnt/"
_NET_SKIP_PREFIXES = ("lo", "veth", "vprobe", "docker", "br-")

NetBytes = dict[str, tuple[int, int]]


def cpu_busy_pct(prev: dict[str, int], cur: dict[str, int]) -> float:
    total = sum(cur.get(k, 0) - prev.get(k, 0) for k in procfs.STAT_FIELDS)
    if total <= 0:
        return 0.0
    idle = (cur.get("idle", 0) - prev.get("idle", 0)) + (cur.get("iowait", 0) - prev.get("iowait", 0))
    return min(100.0, max(0.0, (total - idle) / total * 100))


def parse_uptime(text: str) -> float | None:
    parts = text.split()
    if not parts:
        return None
    try:
        return float(parts[0])
    except ValueError:
        return None


def metric_iface(name: str, has_device: bool, is_tun: bool) -> bool:
    return is_tun or (has_device and not name.startswith(_NET_SKIP_PREFIXES))


def parse_net_bytes(text: str) -> NetBytes:
    out: NetBytes = {}
    for line in text.splitlines():
        name, sep, rest = line.partition(":")
        nums = rest.split()
        if not sep or len(nums) < 16:
            continue
        try:
            out[name.strip()] = (int(nums[0]), int(nums[8]))
        except ValueError:
            continue
    return out


def net_rates(
    prev: NetBytes,
    cur: NetBytes,
    window_s: float,
    include: Callable[[str], bool],
) -> dict[str, tuple[float, float]]:
    window = max(window_s, 1e-3)
    out: dict[str, tuple[float, float]] = {}
    for iface, (rx, tx) in cur.items():
        before = prev.get(iface)
        if before is None or not include(iface):
            continue
        out[iface] = (max(0, rx - before[0]) / window, max(0, tx - before[1]) / window)
    return out


def parse_mount_points(text: str) -> list[str]:
    mounts: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[2] not in _REAL_FILESYSTEMS:
            continue
        point = parts[1].replace("\\040", " ")
        if (point == "/" or point.startswith(_MOUNT_PREFIX)) and point not in mounts:
            mounts.append(point)
    return mounts


def _metric_iface(name: str) -> bool:
    return metric_iface(name, os.path.exists(f"/sys/class/net/{name}/device"), netif.is_tun(name))


def _count_processes() -> int:
    try:
        return sum(1 for entry in os.listdir("/proc") if entry.isdigit())
    except OSError:
        return 0


def count_kernel_stalls(text: str) -> int:
    return sum(1 for line in text.splitlines() if _STALL_PATTERN.search(line))


def _can_read_kernel_journal() -> bool:
    if os.geteuid() == 0:
        return True
    try:
        names = {grp.getgrgid(gid).gr_name for gid in os.getgroups()}
    except (KeyError, OSError):
        return False
    return any(g in names for g in _JOURNAL_GROUPS)


def _read_kernel_stall_count(since: datetime, until: datetime) -> int | None:
    if not KERNEL_JOURNAL_REQUIREMENT.available():
        return None
    if not _can_read_kernel_journal():
        requirements_registry.report(KERNEL_JOURNAL_REQUIREMENT, RequirementStatus.NEEDS_PRIVILEGE)
        return None
    args = [
        "journalctl",
        "-k",
        "--no-pager",
        "-o",
        "cat",
        "-S",
        f"@{int(since.timestamp())}",
        "-U",
        f"@{int(until.timestamp())}",
    ]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("journalctl -k не выполнился: %s", exc)
        return None
    if proc.returncode != 0:
        if looks_like_permission_error(proc.stderr):
            requirements_registry.report(KERNEL_JOURNAL_REQUIREMENT, RequirementStatus.NEEDS_PRIVILEGE)
        else:
            log.warning("journalctl -k код %s: %s", proc.returncode, proc.stderr.strip())
        return None
    requirements_registry.report(KERNEL_JOURNAL_REQUIREMENT, RequirementStatus.OK)
    return count_kernel_stalls(proc.stdout)


def _reading(metric: str, value: float, now: datetime, instance: str | None = None) -> HostMetricReading:
    spec = metric_spec(metric)
    suffix = f":{instance}" if instance is not None else ""
    label = f"{spec.label} {instance}" if instance is not None else spec.label
    return HostMetricReading(
        component_id=f"host:{metric}{suffix}",
        metric=metric,
        label=label,
        value=round(value, 2),
        unit=spec.unit,
        taken_at=now,
    )


def _used_pct(path: str) -> float | None:
    try:
        st = os.statvfs(path)
    except OSError as exc:
        if path == "/":
            log.warning("statvfs(%r) не сработал: %s", path, exc)
        return None
    return (st.f_blocks - st.f_bfree) / st.f_blocks * 100 if st.f_blocks else None


def read_host_sync(
    now: datetime,
    sample_window_s: float = 10.0,
    kernel_since: datetime | None = None,
) -> list[HostMetricReading]:
    paths = ("/proc/stat", "/proc/vmstat", "/proc/net/dev")
    stat1, vm1, netdev1 = (procfs.read_text(p) for p in paths)
    if not stat1:
        return []
    time.sleep(max(0.0, sample_window_s))
    stat2, vm2, netdev2 = (procfs.read_text(p) for p in paths)

    out: list[HostMetricReading] = []

    cpu1, cpu2 = procfs.parse_proc_stat_cpu(stat1), procfs.parse_proc_stat_cpu(stat2)
    steal, iowait = procfs.cpu_deltas(cpu1, cpu2)
    out.append(_reading("cpu_busy_pct", cpu_busy_pct(cpu1, cpu2), now))
    out.append(_reading("steal_pct", steal, now))
    out.append(_reading("iowait_pct", iowait, now))

    loads = procfs.parse_loadavgs(procfs.read_text("/proc/loadavg"))
    if loads is not None:
        out.append(_reading("load_per_core", loads[0] / (os.cpu_count() or 1), now))
        for metric, load in zip(("load1", "load5", "load15"), loads, strict=True):
            out.append(_reading(metric, load, now))

    mi = procfs.parse_meminfo(procfs.read_text("/proc/meminfo"))
    if mi.get("MemTotal"):
        available = mi.get("MemAvailable", 0)
        out.append(_reading("mem_available_pct", available / mi["MemTotal"] * 100, now))
        out.append(_reading("mem_used_pct", (mi["MemTotal"] - available) / mi["MemTotal"] * 100, now))
        out.append(_reading("mem_available_bytes", available * 1024.0, now))
    if mi.get("SwapTotal"):
        used = mi["SwapTotal"] - mi.get("SwapFree", 0)
        out.append(_reading("swap_used_pct", used / mi["SwapTotal"] * 100, now))

    root_pct = _used_pct("/")
    if root_pct is not None:
        out.append(_reading("disk_used_pct", root_pct, now))
    for mount in parse_mount_points(procfs.read_text("/proc/mounts")):
        mount_pct = _used_pct(mount)
        if mount_pct is not None:
            out.append(_reading("disk_pct", mount_pct, now, mount))

    uptime = parse_uptime(procfs.read_text("/proc/uptime"))
    if uptime is not None:
        out.append(_reading("uptime_s", uptime, now))
    out.append(_reading("procs", float(_count_processes()), now))

    for metric, path in (
        ("psi_cpu", "/proc/pressure/cpu"),
        ("psi_memory", "/proc/pressure/memory"),
        ("psi_io", "/proc/pressure/io"),
    ):
        val = procfs.parse_pressure_some_avg60(procfs.read_text(path))
        if val is not None:
            out.append(_reading(metric, val, now))

    window_min = max(sample_window_s, 1.0) / 60.0

    if vm1 and vm2:
        oom = max(
            0, procfs.parse_vmstat(vm2).get("oom_kill", 0) - procfs.parse_vmstat(vm1).get("oom_kill", 0)
        )
        out.append(_reading("oom_kills", oom / window_min, now))

    if netdev1 and netdev2:
        rates = net_rates(parse_net_bytes(netdev1), parse_net_bytes(netdev2), sample_window_s, _metric_iface)
        for iface, (rx_bps, tx_bps) in sorted(rates.items()):
            out.append(_reading("net_rx_bps", rx_bps, now, iface))
            out.append(_reading("net_tx_bps", tx_bps, now, iface))
        delta = procfs.net_err_delta(procfs.parse_proc_net_dev(netdev1), procfs.parse_proc_net_dev(netdev2))
        out.append(_reading("net_err_rate", delta / window_min, now))

    count_text = procfs.read_text("/proc/sys/net/netfilter/nf_conntrack_count")
    max_text = procfs.read_text("/proc/sys/net/netfilter/nf_conntrack_max")
    if count_text and max_text:
        pct = procfs.parse_conntrack(count_text, max_text)
        if pct is not None:
            out.append(_reading("conntrack_pct", pct, now))

    since = kernel_since if kernel_since is not None else now - timedelta(seconds=sample_window_s)
    stalls = _read_kernel_stall_count(since, now)
    if stalls is not None:
        out.append(_reading("kernel_stall_events", float(stalls), now))

    return out
