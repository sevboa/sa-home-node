from __future__ import annotations

import json
from pathlib import Path

import pytest

from sa_home_node.metrics.peerhost import peer_host

FIXTURES = Path(__file__).parent.parent / "fixtures" / "metrics"
CASES = json.loads((FIXTURES / "scrape" / "peer-host.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: "|".join(case["candidates"]) or "empty")
def test_peer_host(case):
    assert peer_host(case["candidates"]) == case["host"]
