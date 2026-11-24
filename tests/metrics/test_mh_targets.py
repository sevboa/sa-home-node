from __future__ import annotations

import json
from pathlib import Path

import pytest

from sa_home_node.metrics.targets import ScrapeTarget, scrape_targets
from sa_home_node.swarm.config import SwarmPeer

FIXTURES = Path(__file__).parent.parent / "fixtures" / "metrics"
CASES = json.loads((FIXTURES / "scrape" / "targets.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_scrape_targets(case):
    peers = [SwarmPeer(**row) for row in [*case["config"], *case["learned"]]]
    expected = [ScrapeTarget(**row) for row in case["expected"]]
    assert scrape_targets(case["own_id"], case["own_host"], peers) == expected
