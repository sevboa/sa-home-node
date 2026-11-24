from __future__ import annotations

import pytest

from sa_home_node.host.linux import LinuxHost
from sa_home_node.host.windows import WindowsHost


@pytest.mark.parametrize(
    ("host", "argv"),
    [
        (LinuxHost(), ["ping", "-c", "1", "-W", "1", "192.0.2.1"]),
        (WindowsHost(), ["ping", "-n", "1", "-w", "1000", "192.0.2.1"]),
    ],
)
def test_ping_argv(host, argv: list[str]):
    assert host.ping_argv("192.0.2.1") == argv
