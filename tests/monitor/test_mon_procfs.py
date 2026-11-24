from __future__ import annotations

import json
from pathlib import Path

import pytest

from sa_home_node.monitor.sensors import procfs

FIXTURES = Path(__file__).parents[1] / "fixtures" / "monitor" / "sensors"
IN = FIXTURES / "in"
REFERENCE = json.loads((FIXTURES / "procfs.json").read_text(encoding="utf-8"))
STAT = "cpu  100 5 50 800 20 3 2 10 7 9\ncpu0 1 1 1 1 1 1 1 1 1 1\n"
NETDEV = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n"
    "    lo: 100 1 9 9 0 0 0 0 100 1 9 9 0 0 0 0\n"
    "  eth0: 1000 10 2 3 0 0 0 0 2000 20 4 5 0 0 0 0\n"
    "  bad0: 1 2 3\n"
    "  bad1: x 10 2 3 0 0 0 0 2000 20 4 5 0 0 0 0\n"
)


def test_proc_stat_excludes_guest():
    assert procfs.parse_proc_stat_cpu(STAT) == {
        "user": 100, "nice": 5, "system": 50, "idle": 800,
        "iowait": 20, "irq": 3, "softirq": 2, "steal": 10,
    }


@pytest.mark.parametrize("text", ["", "cpu0 1 2 3\n", "cpu  a b c\n"])
def test_proc_stat_empty(text):
    assert procfs.parse_proc_stat_cpu(text) == {}


def test_proc_stat_short_line():
    assert procfs.parse_proc_stat_cpu("cpu  1 2 3\n") == {"user": 1, "nice": 2, "system": 3}


@pytest.mark.parametrize(
    ("prev", "cur", "expected"),
    [
        ({"user": 0, "steal": 0, "iowait": 0}, {"user": 80, "steal": 10, "iowait": 10}, (10.0, 10.0)),
        ({"user": 5}, {"user": 5}, (0.0, 0.0)),
        ({"user": 10}, {"user": 5}, (0.0, 0.0)),
        ({"idle": 0, "steal": 5}, {"idle": 100, "steal": 0}, (0.0, 0.0)),
        ({}, {}, (0.0, 0.0)),
    ],
)
def test_cpu_deltas(prev, cur, expected):
    assert procfs.cpu_deltas(prev, cur) == expected


def test_meminfo():
    text = "MemTotal:  1000 kB\nMemAvailable:  400 kB\nHugePages_Total:  0\nbroken: x kB\nnocolon\n"
    assert procfs.parse_meminfo(text) == {"MemTotal": 1000, "MemAvailable": 400, "HugePages_Total": 0}
    assert procfs.parse_meminfo("") == {}


@pytest.mark.parametrize(
    ("raw", "case"),
    [
        (IN.joinpath("loadavg.txt").read_text(encoding="utf-8"), "in/loadavg.txt"),
        ("", "empty"),
        ("abc def ghi", "garbage"),
    ],
)
def test_parse_loadavg_reference(raw, case):
    loads = procfs.parse_loadavgs(raw)
    assert (loads[0] if loads else None) == REFERENCE["parse_loadavg"][case]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("some avg10=0.00 avg60=1.25 avg300=0.50 total=1\nfull avg10=0 avg60=9.0 avg300=0 total=0\n", 1.25),
        ("full avg60=9.0\n", None),
        ("some avg10=0.00 avg60=zz total=1\n", None),
        ("some avg10=0.00 total=1\n", None),
        ("", None),
    ],
)
def test_pressure(text, expected):
    assert procfs.parse_pressure_some_avg60(text) == expected


def test_vmstat():
    assert procfs.parse_vmstat("oom_kill 3\npgfault 10\nbad x\nthree 1 2\n") == {"oom_kill": 3, "pgfault": 10}
    assert procfs.parse_vmstat("") == {}


def test_net_dev_skips_loopback_and_broken_and_keeps_drops():
    assert procfs.parse_proc_net_dev(NETDEV) == {"eth0": (2, 3, 4, 5)}
    assert procfs.parse_proc_net_dev("") == {}


def test_net_err_delta_counts_only_errs():
    prev = {"eth0": (2, 3, 4, 5), "gone": (1, 1, 1, 1)}
    cur = {"eth0": (5, 900, 5, 900), "new": (7, 7, 7, 7)}
    assert procfs.net_err_delta(prev, cur) == 4
    assert procfs.net_err_delta({"eth0": (9, 0, 9, 0)}, {"eth0": (1, 0, 1, 0)}) == 0
    assert procfs.net_err_delta({}, {}) == 0


@pytest.mark.parametrize(
    ("count", "limit", "expected"),
    [("50\n", "200\n", 25.0), ("1", "0", None), ("x", "10", None), ("", "", None), ("1", "-5", None)],
)
def test_conntrack(count, limit, expected):
    assert procfs.parse_conntrack(count, limit) == expected


def test_read_text(tmp_path):
    f = tmp_path / "x"
    f.write_text("abc", encoding="utf-8")
    assert procfs.read_text(str(f)) == "abc"
    assert procfs.read_text(str(tmp_path / "none")) == ""
    f.write_bytes(b"\xff\xfe")
    assert procfs.read_text(str(f)) == ""
