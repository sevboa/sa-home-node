import pytest
from world import golden_json

from sa_home_node.fixups.unit import (
    exec_command,
    rewrite_unit_path_line,
    unit_path_first_dir,
    with_exec_command,
)

BOM = chr(0xFEFF)
CONFIG = "/home/user-a/.config/sa-home-node/config.toml"
NEW_ARGV = ["/home/user-a/.local/bin/sa-home-node", "run", "--config", CONFIG]

SYSTEMD = (
    "[Service]\n"
    "ExecStartPre=/bin/sh -c 'true'\n"
    "ExecStart={exec}\n"
    "Restart=on-failure\n"
    "Environment=PATH=/usr/local/bin:/usr/bin:/bin\n"
)

WINSW = (
    BOM + "<!-- node service -->\r\n"
    "<service>\r\n"
    "  <description>Tools &amp; Co</description>\r\n"
    "  <executable>{exe}</executable>\r\n"
    "  <arguments>{args}</arguments>\r\n"
    "  <workingdirectory>C:\\node</workingdirectory>\r\n"
    "</service>\r\n"
)

WINSW_EXE = "C:\\Tools &amp;amp; Co\\sa-home-node.exe"
WINSW_OLD = WINSW.format(exe=WINSW_EXE, args='--service node --config "C:\\cfg dir\\config.toml"')
WINSW_ARGV = ["C:\\Tools &amp; Co\\sa-home-node.exe", "run", "--config", "C:\\cfg dir\\config.toml"]

SYSTEMD_OLD = SYSTEMD.format(exec=f"/home/user-a/.local/bin/sa-home-node --service node --config {CONFIG}")

CASES = [
    ("systemd-user", SYSTEMD_OLD, SYSTEMD.format(exec=" ".join(NEW_ARGV)), NEW_ARGV),
    ("winsw", WINSW_OLD, WINSW.format(exe=WINSW_EXE, args='run --config "C:\\cfg dir\\config.toml"'), WINSW_ARGV),
]


@pytest.mark.parametrize(("fmt", "old", "new", "argv"), CASES)
def test_with_exec_command_replaces_only_launch_line(fmt, old, new, argv):
    command = exec_command(old, fmt)
    assert command[0] == argv[0] and command[1:3] == ["--service", "node"] and command[-1] == argv[-1]
    assert with_exec_command(old, fmt, argv) == new
    assert exec_command(new, fmt) == argv
    assert with_exec_command(new, fmt, argv) == new


def test_winsw_keeps_bom_and_line_endings():
    result = with_exec_command(WINSW_OLD, "winsw", WINSW_ARGV)
    assert result.startswith(BOM + "<!--")
    assert result.count("\r\n") == WINSW_OLD.count("\r\n")
    assert "<description>Tools &amp; Co</description>" in result


def test_systemd_keeps_crlf_and_quotes_spaces():
    old = SYSTEMD_OLD.replace("\n", "\r\n")
    result = with_exec_command(old, "systemd-user", ["/opt/my node/bin", "run"])
    assert "ExecStart='/opt/my node/bin' run\r\n" in result
    assert result.count("\r\n") == old.count("\r\n")
    assert exec_command(result, "systemd-user") == ["/opt/my node/bin", "run"]


@pytest.mark.parametrize("fmt", ["systemd-user", "winsw"])
def test_unrecognisable_unit(fmt):
    assert exec_command("[Unit]\nDescription=x\n", fmt) is None
    assert with_exec_command("[Unit]\nDescription=x\n", fmt, ["a"]) is None


UNIT_PATH = golden_json("unit-path.json")


@pytest.mark.parametrize("case", UNIT_PATH, ids=[c["name"] for c in UNIT_PATH])
def test_rewrite_unit_path_line_golden(case):
    assert rewrite_unit_path_line(case["before"], case["wrapper_dir"]) == case["after"]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("Environment=PATH=/w:/usr/bin\n", "/w"),
        ("Environment=PATH=\n", None),
        ("Type=simple\n", None),
    ],
)
def test_unit_path_first_dir(content, expected):
    assert unit_path_first_dir(content) == expected
