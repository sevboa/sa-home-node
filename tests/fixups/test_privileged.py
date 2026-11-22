from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest
from world import assert_golden, golden_json

from sa_home_node import host
from sa_home_node.fixups import power, privileged
from sa_home_node.fixups.core import FixupError
from sa_home_node.host.windows import WindowsHost


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(host, "current", lambda: WindowsHost())


def denied(path, *args, **kwargs):
    raise PermissionError(13, "Permission denied", str(path))


def test_read_text_if_exists_and_file_matches(root):
    root.files["/etc/x"] = "body"
    assert privileged.read_text_if_exists(Path("/etc/x")) == "body"
    assert privileged.read_text_if_exists(Path("/etc/none")) is None
    assert privileged.file_matches(Path("/etc/x"), "body") is True
    assert privileged.file_matches(Path("/etc/x"), "other") is False
    assert privileged.file_matches(Path("/etc/none"), "body") is False


def test_unit_is_active(root):
    root.active_units = {"a.service"}
    assert privileged.unit_is_active("a.service") is True
    assert privileged.unit_is_active("b.service", user=True) is False
    assert root.calls[-1] == ["systemctl", "--user", "is-active", "--quiet", "b.service"]


@pytest.fixture
def sbin_dir(tmp_path, monkeypatch):
    tool = tmp_path / "tool-x"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(privileged, "SBIN_FALLBACK_DIRS", (str(tmp_path),))
    return tmp_path


def test_find_executable_falls_back_to_sbin_dirs(monkeypatch, sbin_dir):
    monkeypatch.setenv("PATH", "/nonexistent")
    assert privileged.find_executable("tool-x") == str(sbin_dir / "tool-x")
    assert privileged.find_executable("tool-missing") is None


def test_find_executable_skips_excluded_dir(monkeypatch, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    for directory in (first, second):
        directory.mkdir()
        tool = directory / "tool-y"
        tool.write_text("#!/bin/sh\n")
        tool.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(first), str(second)]))
    assert privileged.find_executable("tool-y") == str(first / "tool-y")
    assert privileged.find_executable("tool-y", exclude_dir=str(first)) == str(second / "tool-y")


@pytest.mark.parametrize(
    "call",
    [
        lambda: privileged.run_sudo(["true"]),
        lambda: privileged.daemon_reload(),
        lambda: privileged.install_root_file(Path("/etc/x"), "b", mode="0644"),
        lambda: privileged.path_exists(Path("/etc/x")),
        lambda: privileged.read_text(Path("/etc/x")),
    ],
)
def test_privileged_calls_fail_without_elevation(windows, call, monkeypatch):
    monkeypatch.setattr(Path, "exists", denied)
    with pytest.raises(FixupError, match="повышение прав"):
        call()


def test_run_user_reports_failure(root):
    root.outputs["false"] = None
    with pytest.raises(FixupError, match="завершился кодом 1"):
        privileged.run_user(["false"])
    privileged.run_user(["true"])


OTHER_MANAGERS = {"apt", "pacman", "dnf", "zypper", "apk", "brew"}
UNIT = Path("/etc/systemd/system/unit-a.service")
PRIVATE = Path("/etc/private-a/file")
NO_MANAGER = "нет менеджера"
POLKIT_DIRS = {"/etc/polkit-1/rules.d"}
POWER_APPLY = power.power_fixups(None)[0].apply


def scenario(action, **setup):
    return {"action": action, **setup}


def package(missing):
    return scenario(lambda: privileged.install_package("pkg-a", NO_MANAGER), missing_tools=missing)


SCENARIOS = {
    "install_root_file": scenario(lambda: privileged.install_root_file(Path("/etc/file-a.conf"), "a\n", mode="0644")),
    "install_root_file_parents": scenario(
        lambda: privileged.install_root_file(Path("/etc/dir-a/file-a.conf"), "a\n", mode="0755", make_parents=True)
    ),
    "install_executable": scenario(
        lambda: privileged.install_executable(Path("/home/user-a/bin-a"), "/usr/local/bin/bin-a")
    ),
    "sudoers_accepted": scenario(
        lambda: privileged.install_sudoers_snippet("50-test-a", "user-a ALL=(root) NOPASSWD: /bin/true\n")
    ),
    "sudoers_rejected": scenario(
        lambda: privileged.install_sudoers_snippet("50-test-a", "битый\n"), reject_visudo=True
    ),
    "sudoers_no_visudo": scenario(
        lambda: privileged.install_sudoers_snippet("50-test-a", "x\n"), missing_tools={"visudo"}
    ),
    "path_exists_denied_present": scenario(
        lambda: privileged.path_exists(PRIVATE), files={str(PRIVATE): ""}, denied={str(PRIVATE)}
    ),
    "path_exists_denied_absent": scenario(lambda: privileged.path_exists(PRIVATE), denied={str(PRIVATE)}),
    "path_exists_plain": scenario(lambda: privileged.path_exists(Path("/etc/file-a")), files={"/etc/file-a": ""}),
    "install_package_apt": package(OTHER_MANAGERS - {"apt"}),
    "install_package_dnf": package(OTHER_MANAGERS - {"dnf"}),
    "install_package_none": package(OTHER_MANAGERS),
    "replace_unit_new": scenario(lambda: privileged.replace_unit(UNIT, "[Unit]\n")),
    "replace_unit_same": scenario(lambda: privileged.replace_unit(UNIT, "[Unit]\n"), files={str(UNIT): "[Unit]\n"}),
    "replace_unit_other": scenario(
        lambda: privileged.replace_unit(UNIT, "[Unit]\n"), files={str(UNIT): "[Unit]\nold\n"}
    ),
    "daemon_reload": scenario(privileged.daemon_reload),
    "enable_now": scenario(lambda: privileged.enable_now("unit-a.service")),
    "power_polkit_with_rules_dir": scenario(POWER_APPLY, dirs=POLKIT_DIRS),
    "power_polkit_without_rules_dir": scenario(POWER_APPLY, missing_tools=OTHER_MANAGERS - {"apt"}),
}
ARGV = [e for e in golden_json("argv.json") if e["name"] in SCENARIOS]


def test_argv_golden_covers_scenarios():
    assert {e["name"] for e in ARGV} == set(SCENARIOS)


@pytest.mark.parametrize("expected", ARGV, ids=[e["name"] for e in ARGV])
def test_argv_golden(root, monkeypatch, tmp_path, expected):
    setup = dict(SCENARIOS[expected["name"]])
    action = setup.pop("action")
    root.files.update(setup.get("files", {}))
    root.dirs.update(setup.get("dirs", ()))
    root.denied.update(setup.get("denied", ()))
    root.missing_tools.update(setup.get("missing_tools", ()))
    root.reject_visudo = setup.get("reject_visudo", False)
    monkeypatch.setattr(power, "getuser", lambda: "user-a")
    actual = {"name": expected["name"]}
    try:
        value = action()
        if value is not None:
            actual["result"] = value
    except FixupError as exc:
        actual["error"] = str(exc)
    tmp = tempfile.gettempdir() + "/"
    actual["calls"] = [["<tmp>" if a.startswith(tmp) else a for a in call] for call in root.calls]
    actual["installed"] = root.installed
    assert_golden(actual, expected)
