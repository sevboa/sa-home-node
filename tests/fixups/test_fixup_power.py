from __future__ import annotations

import pytest

from sa_home_node.fixups import power
from sa_home_node.fixups.core import FixupError

RULE_PATH = "/etc/polkit-1/rules.d/50-sa-home-node-power.rules"
MANAGERS = {"apt", "pacman", "dnf", "zypper", "apk", "brew"}
fixup = power.power_fixups(None)[0]


def test_check_is_presence_only(root):
    assert fixup.check() is False
    root.files[RULE_PATH] = "other"
    assert fixup.check() is True


def test_apply_without_package_manager(root):
    root.missing_tools = MANAGERS
    with pytest.raises(FixupError, match="polkitd"):
        fixup.apply()
