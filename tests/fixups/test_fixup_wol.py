from __future__ import annotations

from types import SimpleNamespace

import pytest

from sa_home_node import wol
from sa_home_node.fixups import wol as wolfix
from sa_home_node.fixups.core import FixupError

UNIT_PATH = "/etc/systemd/system/sa-home-wol.service"
MANAGERS = {"apt", "pacman", "dnf", "zypper", "apk", "brew"}
fixup = wolfix.wol_fixups(None)[0]


@pytest.fixture
def iface(monkeypatch):
    monkeypatch.setattr(wol, "detect_local_wake_iface", lambda: "enp1s0")


def test_unit_content():
    assert wolfix.wol_unit_content("/usr/sbin/ethtool", "enp1s0") == (
        "[Unit]\n"
        "Description=sa-home-bot: включить Wake-on-LAN (magic packet)\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/usr/sbin/ethtool -s enp1s0 wol g\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def test_meta():
    assert fixup.id == "wol-enable"
    assert fixup.platforms == ("linux",)


@pytest.mark.parametrize("wakeable", [True, False])
def test_needed(wakeable):
    ctx = SimpleNamespace(traits=SimpleNamespace(wakeable=wakeable))
    assert fixup.needed(ctx) is wakeable


def test_apply_installs_and_is_idempotent(root, iface):
    assert fixup.check() is False
    fixup.apply()
    assert root.installed[UNIT_PATH] == wolfix.wol_unit_content("/usr/bin/ethtool", "enp1s0")
    assert ["systemctl", "daemon-reload"] in root.sudo_calls
    assert ["systemctl", "enable", "--now", "sa-home-wol.service"] in root.sudo_calls
    assert fixup.check() is True


def test_check_keeps_manual_unit(root):
    root.files[UNIT_PATH] = "manual"
    assert fixup.check() is True


def test_apply_without_iface(root, monkeypatch):
    monkeypatch.setattr(wol, "detect_local_wake_iface", lambda: None)
    with pytest.raises(FixupError, match="WoL настраивать не на чем"):
        fixup.apply()
    assert not root.sudo_calls


def test_apply_ethtool_missing_everywhere(root, iface):
    root.missing_tools = MANAGERS | {"ethtool"}
    with pytest.raises(FixupError, match="ethtool"):
        fixup.apply()


def test_apply_ethtool_still_missing_after_install(root, iface):
    root.missing_tools = {"ethtool"}
    with pytest.raises(FixupError, match="после установки"):
        fixup.apply()
    assert any(c[0] == "apt-get" or c[0] == "apt" for c in root.sudo_calls)
