from __future__ import annotations

from pathlib import Path

SYS_CLASS_NET = Path("/sys/class/net")


def is_tun(iface: str, root: Path = SYS_CLASS_NET) -> bool:
    return (root / iface / "tun_flags").exists()
