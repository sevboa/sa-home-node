from __future__ import annotations

from pathlib import Path

import pytest

from sa_home_node.metrics.filesd import file_sd_text
from sa_home_node.metrics.targets import ScrapeTarget

FIXTURES = Path(__file__).parent.parent / "fixtures" / "metrics"
TARGETS = [
    ScrapeTarget("n1", "100.64.0.1"),
    ScrapeTarget("n2", "100.64.0.2"),
    ScrapeTarget("n3", "192.168.1.3"),
]


@pytest.mark.parametrize(
    ("targets", "port", "fixture"),
    [
        (TARGETS, 9100, "file-sd-nodes.json"),
        (TARGETS[1:2], 9101, "file-sd-extra.json"),
    ],
)
def test_file_sd_matches_fixture(targets, port, fixture):
    expected = (FIXTURES / "scrape" / fixture).read_text(encoding="utf-8")
    assert file_sd_text(targets, port) == expected


def test_empty_list():
    assert file_sd_text([], 9100) == "[]\n"
