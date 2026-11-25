from __future__ import annotations

import pytest
from ctlfix import load

from sa_home_node.swarm.peerpath import format_peer_path

CASES = load("peerpath.json")["cases"]


@pytest.mark.parametrize("case", CASES, ids=[str(i) for i in range(len(CASES))])
def test_peer_path_matches_prototype(case):
    assert format_peer_path(case["peer"]) == case["text"]
