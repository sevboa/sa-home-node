from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from sa_home_node import requirements
from sa_home_node.monitor.sensors import hostscan, procfs

FIXTURES = Path(__file__).parents[1] / "fixtures" / "monitor" / "sensors"
IN = FIXTURES / "in"
GAUGES = json.loads((FIXTURES / "host-gauges.json").read_text(encoding="utf-8"))
SCAN = json.loads((FIXTURES / "host-scan.json").read_text(encoding="utf-8"))
STALLS = json.loads((FIXTURES / "kernel-stalls.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
JOURNAL = "kernel: hung_task x\nwatchdog: BUG: soft lockup\nNETDEV WATCHDOG: eth0\nfine\n"


def text(name: str) -> str:
    return (IN / f"{name}.txt").read_text(encoding="utf-8")


def stamp(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp())


@pytest.mark.parametrize(
    ("prev", "cur", "case"),
    [
        (
            procfs.parse_proc_stat_cpu(text("proc-stat-1")),
            procfs.parse_proc_stat_cpu(text("proc-stat-2")),
            "normal",
        ),
        ({"user": 5}, {"user": 5}, "same"),
        (
            procfs.parse_proc_stat_cpu(text("proc-stat-2")),
            procfs.parse_proc_stat_cpu(text("proc-stat-1")),
            "counter_reset",
        ),
        ({}, procfs.parse_proc_stat_cpu(text("proc-stat-2")), "empty_prev"),
        ({}, {}, "both_empty"),
        ({"idle": 0}, {"idle": 100}, "all_idle"),
        ({"iowait": 0}, {"iowait": 100}, "iowait_counts_as_idle"),
        ({"user": 0}, {"user": 100}, "all_busy"),
        ({"idle": 100}, {"idle": 50, "user": 50}, "idle_went_back"),
    ],
)
def test_cpu_busy_pct(prev, cur, case):
    assert hostscan.cpu_busy_pct(prev, cur) == GAUGES["cpu_busy_pct"][case]


@pytest.mark.parametrize(
    ("raw", "case"),
    [
        (text("loadavg"), "in/loadavg.txt"),
        ("", "empty"),
        ("abc def ghi", "garbage"),
        ("1.0 2.0", "two_fields"),
        ("1.0 x 3.0", "partial_garbage"),
        ("1 2 3", "integers"),
        ("0.01 0.02 0.03 1/2 3", "only_three"),
        ("-1 0.5 0.5", "negative"),
    ],
)
def test_parse_loadavgs(raw, case):
    expected = GAUGES["parse_loadavgs"][case]
    assert procfs.parse_loadavgs(raw) == (tuple(expected) if expected else None)


@pytest.mark.parametrize(
    ("raw", "case"),
    [
        (text("uptime"), "in/uptime.txt"),
        ("", "empty"),
        ("  \n", "whitespace"),
        ("abc", "garbage"),
        ("42", "single_field"),
        ("0 0", "zero"),
    ],
)
def test_parse_uptime(raw, case):
    assert hostscan.parse_uptime(raw) == GAUGES["parse_uptime"][case]


@pytest.mark.parametrize(
    ("raw", "case"),
    [
        (text("net-dev-1"), "in/net-dev-1.txt"),
        (text("net-dev-2"), "in/net-dev-2.txt"),
        ("", "empty"),
        ("Inter-|  Receive\n face |bytes\n", "header_only"),
        ("eth0 1 2 3\n", "line_without_colon"),
        ("eth0: 18446744073709551615 0 0 0 0 0 0 0 18446744073709551614 0 0 0 0 0 0 0\n", "huge_counters"),
        (
            "eth0: -5 0 0 0 0 0 0 0 7 0 0 0 0 0 0 0\nbad: x 0 0 0 0 0 0 0 7 0 0 0 0 0 0 0\n",
            "negative_and_text",
        ),
        ("e: 1 0 0 0 0 0 0 0 2 0 0 0 0 0 0 0\n", "exactly_16_fields"),
        ("e: 1 0 0 0 0 0 0 2 0 0 0 0 0 0 0\n", "fifteen_fields"),
    ],
)
def test_parse_net_bytes(raw, case):
    expected = {k: tuple(v) for k, v in GAUGES["parse_net_bytes"][case].items()}
    assert hostscan.parse_net_bytes(raw) == expected


NET1 = hostscan.parse_net_bytes(text("net-dev-1"))
NET2 = hostscan.parse_net_bytes(text("net-dev-2"))
ETH = {"eth0": (0, 0)}


@pytest.mark.parametrize(
    ("prev", "cur", "window", "include", "case"),
    [
        (NET1, NET2, 10.0, lambda n: True, "normal_all"),
        (NET1, NET2, 10.0, lambda n: n.startswith("eth"), "normal_eth_only"),
        (NET1, NET2, 10.0, lambda n: False, "include_none"),
        (ETH, {"eth0": (1000, 1000)}, 0.0, lambda n: True, "window_zero"),
        (ETH, {"eth0": (1000, 1000)}, -5.0, lambda n: True, "window_negative"),
        (ETH, {"eth0": (1000, 1000)}, 2.5, lambda n: True, "window_fraction"),
        ({}, NET2, 10.0, lambda n: True, "empty_prev"),
        (NET1, {}, 10.0, lambda n: True, "empty_cur"),
        ({"eth0": (0, 0), "gone": (1, 1)}, {"eth0": (200, 400)}, 10.0, lambda n: True, "iface_vanished"),
        ({"eth0": (0, 0)}, {"eth0": (200, 400), "new": (5, 5)}, 10.0, lambda n: True, "iface_appeared"),
        ({"eth0": (500, 0)}, {"eth0": (0, 500)}, 10.0, lambda n: True, "counter_reset_rx"),
        ({"eth0": (0, 500)}, {"eth0": (500, 0)}, 10.0, lambda n: True, "counter_reset_tx"),
        (NET2, NET2, 10.0, lambda n: True, "same"),
    ],
)
def test_net_rates(prev, cur, window, include, case):
    expected = GAUGES["net_rates"][case]
    assert hostscan.net_rates(prev, cur, window, include) == {k: tuple(v) for k, v in expected.items()}


@pytest.mark.parametrize(
    ("raw", "case"),
    [(text("mounts"), "in/mounts.txt"), (text("mounts-edge"), "in/mounts-edge.txt"), ("", "empty")],
)
def test_parse_mount_points(raw, case):
    assert hostscan.parse_mount_points(raw) == GAUGES["parse_mount_points"][case]


@pytest.mark.parametrize(
    ("group", "is_tun"),
    [("table", False), ("overlay_vpn0", None)],
)
def test_metric_iface(group, is_tun):
    for row in GAUGES["metric_iface"][group]:
        tun = row["name"] == "vpn0" if is_tun is None else is_tun
        assert hostscan.metric_iface(row["name"], row["has_device"], tun) is row["metric"], row


def test_count_kernel_stalls():
    assert hostscan.count_kernel_stalls(text("kernel-journal")) == STALLS["in/kernel-journal.txt"]
    assert hostscan.count_kernel_stalls("") == STALLS["empty"]


@pytest.fixture
def journal(monkeypatch):
    requirements.requirements_registry.reset()
    calls: list[list[str]] = []
    state = SimpleNamespace(
        which="/usr/bin/journalctl",
        euid=1000,
        groups=[1],
        names={1: "systemd-journal"},
        result=subprocess.CompletedProcess([], 0, text("kernel-journal"), ""),
        platform="linux",
    )

    def run(args, **kwargs):
        calls.append(args)
        if isinstance(state.result, Exception):
            raise state.result
        return state.result

    def getgrgid(gid):
        return SimpleNamespace(gr_name=state.names[gid])

    monkeypatch.setattr(requirements.shutil, "which", lambda name: state.which)
    monkeypatch.setattr(requirements.host, "current", lambda: SimpleNamespace(name=state.platform))
    monkeypatch.setattr(hostscan.subprocess, "run", run)
    monkeypatch.setattr(hostscan.os, "geteuid", lambda: state.euid)
    monkeypatch.setattr(hostscan.os, "getgroups", lambda: state.groups)
    monkeypatch.setattr(hostscan.grp, "getgrgid", getgrgid)
    state.calls = calls
    yield state
    requirements.requirements_registry.reset()


@pytest.mark.parametrize(
    ("case", "euid", "groups"),
    [
        ("root", 0, [5]),
        ("journal_group", 1000, [1]),
        ("adm_group", 1000, [2]),
        ("other_group", 1000, [3]),
        ("unknown_gid", 1000, [9]),
    ],
)
def test_can_read_kernel_journal(journal, case, euid, groups):
    journal.names = {1: "systemd-journal", 2: "adm", 3: "users"}
    journal.euid, journal.groups = euid, groups
    assert hostscan._can_read_kernel_journal() is STALLS["can_read_kernel_journal"][case]


def mutate(case):
    def ok(j):
        pass

    def ok_clean(j):
        j.result = subprocess.CompletedProcess([], 0, "fine\n", "")

    def no_journalctl(j):
        j.which = None

    def not_linux(j):
        j.platform = "windows"

    def no_group(j):
        j.groups = [3]
        j.names[3] = "users"

    def permission_error(j):
        j.result = subprocess.CompletedProcess([], 1, "", "Permission denied")

    def other_error(j):
        j.result = subprocess.CompletedProcess([], 2, "", "boom")

    def oserror(j):
        j.result = OSError("x")

    def timeout(j):
        j.result = subprocess.TimeoutExpired("journalctl", 10)

    return locals()[case]


@pytest.mark.parametrize("case", list(STALLS["read_kernel_stall_count"]))
def test_read_kernel_stall_count(journal, case):
    mutate(case)(journal)
    since = datetime(2026, 1, 15, 11, 59, 50, tzinfo=UTC)
    expected = STALLS["read_kernel_stall_count"][case]
    count = hostscan._read_kernel_stall_count(since, NOW)
    assert count == expected["count"]
    assert journal.calls == expected["calls"]
    problem = requirements.requirements_registry.problem_for(hostscan.KERNEL_JOURNAL_REQUIREMENT)
    assert problem == expected["problem"]


def fake_reader(files: dict[str, tuple[str, str]]):
    seen: dict[str, int] = {}

    def read(path: str) -> str:
        seen[path] = seen.get(path, 0) + 1
        pair = files.get(path)
        if pair is None:
            return ""
        return pair[0] if seen[path] == 1 else pair[1]

    return read


def scan_files():
    both = lambda name: (text(name), text(name))
    pair = lambda name: (text(f"{name}-1"), text(f"{name}-2"))
    files = {
        "/proc/stat": pair("proc-stat"),
        "/proc/vmstat": pair("vmstat"),
        "/proc/net/dev": pair("net-dev"),
        "/proc/loadavg": both("loadavg"),
        "/proc/meminfo": both("meminfo"),
        "/proc/mounts": both("mounts"),
        "/proc/uptime": both("uptime"),
        "/proc/pressure/cpu": both("pressure-cpu"),
        "/proc/pressure/memory": both("pressure-nofull"),
        "/proc/pressure/io": both("pressure-cpu"),
        "/proc/sys/net/netfilter/nf_conntrack_count": ("25\n", "25\n"),
        "/proc/sys/net/netfilter/nf_conntrack_max": ("800\n", "800\n"),
    }
    return files


@pytest.fixture
def scan(monkeypatch, journal):
    sleeps: list[float] = []
    state = SimpleNamespace(
        files=scan_files(),
        cpus=2,
        usage={"/": (25, 100), "/mnt/data": (75, 100), "/mnt/my disk": None},
        procs=["1", "2", "self", "3", "4"],
        tun=set(),
        sleeps=sleeps,
    )

    def statvfs(path):
        pair = state.usage.get(path)
        if pair is None:
            raise OSError(path)
        return SimpleNamespace(f_blocks=pair[1], f_bfree=pair[0])

    def listdir(path):
        if state.procs is None:
            raise OSError(path)
        return state.procs

    real_exists = os.path.exists
    monkeypatch.setattr(procfs, "read_text", lambda path: state.reader(path))
    monkeypatch.setattr(hostscan.time, "sleep", sleeps.append)
    monkeypatch.setattr(hostscan.os, "cpu_count", lambda: state.cpus)
    monkeypatch.setattr(hostscan.os, "statvfs", statvfs)
    monkeypatch.setattr(hostscan.os, "listdir", listdir)
    monkeypatch.setattr(
        hostscan.os.path,
        "exists",
        lambda p: (
            str(p).endswith("/device")
            and p.split("/")[-2] in ("eth0", "eth1")
            or (real_exists(p) if not str(p).startswith("/sys/class/net/") else False)
        ),
    )
    monkeypatch.setattr(hostscan.netif, "is_tun", lambda name: name in state.tun)
    state.reader = fake_reader(state.files)
    journal.result = subprocess.CompletedProcess([], 0, JOURNAL, "")
    state.journal = journal
    return state


def rows(readings):
    return [{**asdict(r), "taken_at": r.taken_at.isoformat()} for r in readings]


def run_windows(state):
    state.reader = fake_reader(state.files)
    default = hostscan.read_host_sync(NOW)
    explicit_since = datetime(2026, 1, 15, 11, 55, 0, tzinfo=UTC)
    explicit = hostscan.read_host_sync(NOW, 30.0, explicit_since)
    return default, explicit


def check(state, name):
    default, explicit = run_windows(state)
    expected = SCAN[name]
    assert rows(default) == expected["default_window"]
    assert rows(explicit) == expected["explicit_window"]
    assert state.sleeps == [expected["calls"][0], expected["calls"][2]]
    stamps = [[stamp(v) for v in expected["calls"][1]], [stamp(v) for v in expected["calls"][3]]]
    if state.journal.which:
        assert [[int(c[6][1:]), int(c[8][1:])] for c in state.journal.calls] == stamps


def test_full(scan):
    check(scan, "full")


def test_full_overlay(scan):
    scan.tun = {"vpn0"}
    check(scan, "full_overlay")


def test_minimal_vps(scan):
    for key in ("vmstat", "mounts", "uptime", "pressure/cpu", "pressure/memory", "pressure/io"):
        scan.files.pop(f"/proc/{key}")
    scan.files.pop("/proc/sys/net/netfilter/nf_conntrack_count")
    scan.files["/proc/meminfo"] = (text("meminfo"), text("meminfo"))
    scan.cpus = 1
    scan.usage = {}
    scan.procs = None
    scan.journal.which = None
    check(scan, "minimal_vps")


def test_no_meminfo_swap(scan):
    for key in ("meminfo", "mounts", "uptime", "pressure/cpu", "pressure/memory", "pressure/io"):
        scan.files.pop(f"/proc/{key}")
    scan.files.pop("/proc/sys/net/netfilter/nf_conntrack_count")
    scan.cpus = 1
    scan.usage = {}
    scan.procs = None
    scan.journal.result = subprocess.CompletedProcess([], 0, "fine\n", "")
    check(scan, "no_meminfo_swap")


def test_mounts_without_uptime(scan):
    scan.files.pop("/proc/uptime")
    scan.cpus = 4
    scan.usage = {"/": (100, 100), "/mnt/data": (75, 100)}
    scan.procs = ["1"]
    scan.journal.result = subprocess.CompletedProcess([], 0, "fine\n", "")
    check(scan, "mounts_without_uptime")


def test_no_proc_stat(scan):
    scan.files.pop("/proc/stat")
    scan.reader = fake_reader(scan.files)
    assert hostscan.read_host_sync(NOW) == SCAN["no_proc_stat"]
    assert scan.sleeps == []
