from __future__ import annotations

import functools
import sys

from .base import Host
from .linux import LinuxHost
from .windows import WindowsHost


def select(platform: str) -> Host:
    if platform == "win32":
        return WindowsHost()
    return LinuxHost()


@functools.cache
def current() -> Host:
    return select(sys.platform)
