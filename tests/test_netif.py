from __future__ import annotations

from sa_home_node.netif import is_tun


def test_is_tun_by_tun_flags(tmp_path):
    (tmp_path / "vpn0").mkdir()
    (tmp_path / "vpn0" / "tun_flags").write_text("0x1001\n")
    (tmp_path / "eth0").mkdir()
    assert is_tun("vpn0", tmp_path)
    assert not is_tun("eth0", tmp_path)
    assert not is_tun("missing", tmp_path)
