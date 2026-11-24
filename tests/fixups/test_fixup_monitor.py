from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from world import golden_bytes, make_config

from sa_home_node.fixups import monitor
from sa_home_node.fixups.core import FixupContext, FixupError
from sa_home_node.fixups.registry import CORE_FIXUPS
from sa_home_node.host import select as select_host
from sa_home_node.host.base import NodeUnit

IDS = ["install-smartmontools", "smartctl-sudoers", "node-unit-smartctl-path", "journalctl-group"]
SUDOERS = "/etc/sudoers.d/50-sa-home-node-smartctl"
UNIT_BEFORE = "[Service]\nEnvironment=PATH=/usr/local/bin:/usr/bin\nExecStart=/bin/true\n"
MANAGERS = {"apt", "pacman", "dnf", "zypper", "apk", "brew"}
DISKS_OFF = "\n[sensors.disks]\nenabled = false\n"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: path)
    monkeypatch.setattr(monitor, "getuser", lambda: "user-a")
    return path


def make_ctx(tmp_path, home, fmt="systemd-user"):
    unit = NodeUnit(fmt, home / "unit" / "sa-home-node.service", ["systemctl", "--user", "daemon-reload"])
    ctx = SimpleNamespace(host=SimpleNamespace(node_unit=lambda path: unit), config=SimpleNamespace(path=Path("/c")))
    return monitor.monitor_fixups(ctx), unit


def by_id(tmp_path, home, fid):
    fixups, unit = make_ctx(tmp_path, home)
    return next(f for f in fixups if f.id == fid), unit


def real_ctx(tmp_path, kind, assigned, extra="") -> FixupContext:
    config = make_config(tmp_path, f'kind = "{kind}"{extra}', ["monitor"] if assigned else [])
    return FixupContext.build(config, select_host("linux"))


def test_registered_between_autoupdate_and_power() -> None:
    names = [f.__name__ for f in CORE_FIXUPS]
    assert names.index("monitor_fixups") == names.index("autoupdate_fixups") + 1
    assert names.index("monitor_fixups") < names.index("power_fixups")


@pytest.mark.parametrize("kind", ("server", "workstation", "vps"))
@pytest.mark.parametrize("assigned", (False, True))
@pytest.mark.parametrize("extra", ("", DISKS_OFF))
def test_needed_matrix(tmp_path, kind, assigned, extra) -> None:
    ctx = real_ctx(tmp_path, kind, assigned, extra)
    fixups = monitor.monitor_fixups(ctx)
    assert [f.id for f in fixups] == IDS
    disks = assigned and not extra and kind != "vps"
    assert [f.needed(ctx) for f in fixups] == [disks, disks, disks, assigned]


def test_wrapper_and_sudoers_content_match_golden() -> None:
    assert monitor.smartctl_sudoers_content("/usr/sbin/smartctl", "user-a").encode() == golden_bytes(
        "files/smartctl.sudoers"
    )
    assert monitor.smartctl_wrapper_content("/usr/sbin/smartctl").encode() == golden_bytes(
        "files/smartctl.wrapper"
    )


@pytest.mark.parametrize(("manager", "ok"), [("apt", True), (None, False)])
def test_install_smartmontools(tmp_path, home, root, manager, ok) -> None:
    fixup, _ = by_id(tmp_path, home, "install-smartmontools")
    root.missing_tools = MANAGERS - {manager}
    if ok:
        fixup.apply()
        assert any("smartmontools" in c for c in root.sudo_calls)
    else:
        with pytest.raises(FixupError, match="пакетный менеджер"):
            fixup.apply()
    root.missing_tools |= {"smartctl"}
    assert fixup.check() is False


def test_sudoers_apply_installs_both(tmp_path, home, root) -> None:
    fixup, _ = by_id(tmp_path, home, "smartctl-sudoers")
    assert fixup.check() is False
    fixup.apply()
    wrapper = home / ".local" / "bin" / "smartctl"
    assert root.installed[SUDOERS].encode() == golden_bytes("files/smartctl.sudoers").replace(
        b"/usr/sbin/smartctl", b"/usr/bin/smartctl"
    )
    assert wrapper.read_text() == monitor.smartctl_wrapper_content("/usr/bin/smartctl")
    assert wrapper.stat().st_mode & 0o777 == 0o755
    assert fixup.check() is True


def test_sudoers_apply_keeps_existing(tmp_path, home, root) -> None:
    fixup, _ = by_id(tmp_path, home, "smartctl-sudoers")
    wrapper = home / ".local" / "bin" / "smartctl"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("custom")
    root.files[SUDOERS] = "custom"
    assert fixup.check() is True
    fixup.apply()
    assert wrapper.read_text() == "custom"
    assert root.installed == {}


def test_sudoers_apply_without_smartctl(tmp_path, home, root) -> None:
    fixup, _ = by_id(tmp_path, home, "smartctl-sudoers")
    root.missing_tools = {"smartctl"}
    with pytest.raises(FixupError, match="install-smartmontools"):
        fixup.apply()


def test_real_path_excludes_wrapper_dir(home, monkeypatch) -> None:
    seen = {}

    def fake(name, *, exclude_dir=None):
        seen["exclude"] = exclude_dir
        return "/usr/sbin/smartctl"

    monkeypatch.setattr(monitor, "find_executable", fake)
    assert monitor.real_smartctl_path() == "/usr/sbin/smartctl"
    assert seen["exclude"] == str(home / ".local" / "bin")


def test_unit_path_check(tmp_path, home) -> None:
    fixup, unit = by_id(tmp_path, home, "node-unit-smartctl-path")
    assert fixup.check() is True
    unit.path.parent.mkdir(parents=True)
    unit.path.write_text(UNIT_BEFORE)
    assert fixup.check() is False
    unit.path.write_text(UNIT_BEFORE.replace("/usr/local/bin", str(home / ".local" / "bin")))
    assert fixup.check() is True


@pytest.mark.parametrize("restart_code", (0, 1))
def test_unit_path_apply(tmp_path, home, root, caplog, restart_code) -> None:
    fixup, unit = by_id(tmp_path, home, "node-unit-smartctl-path")
    unit.path.parent.mkdir(parents=True)
    unit.path.write_text(UNIT_BEFORE)
    root.outputs["systemctl --user restart sa-home-node.service"] = "" if restart_code == 0 else None
    fixup.apply()
    bin_dir = str(home / ".local" / "bin")
    assert unit.path.read_text() == UNIT_BEFORE.replace("PATH=", f"PATH={bin_dir}:")
    assert ["systemctl", "--user", "daemon-reload"] in root.calls
    assert ("не сработал" in caplog.text) is (restart_code != 0)
    assert fixup.check() is True


def test_unit_path_apply_without_path_line(tmp_path, home, root) -> None:
    fixup, unit = by_id(tmp_path, home, "node-unit-smartctl-path")
    unit.path.parent.mkdir(parents=True)
    unit.path.write_text("[Service]\nExecStart=/bin/true\n")
    with pytest.raises(FixupError, match="Environment=PATH="):
        fixup.apply()


@pytest.mark.parametrize(("groups", "expected"), [("user-a sudo systemd-journal", True), ("user-a sudo", False)])
def test_journal_group(tmp_path, home, root, caplog, groups, expected) -> None:
    fixup, _ = by_id(tmp_path, home, "journalctl-group")
    root.outputs["id -nG user-a"] = groups
    assert fixup.check() is expected
    fixup.apply()
    assert ["usermod", "-aG", "systemd-journal", "user-a"] in root.sudo_calls
    assert "systemctl restart user@" in caplog.text
