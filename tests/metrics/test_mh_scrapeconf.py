from __future__ import annotations

from pathlib import Path

import pytest

from sa_home_node.metrics.scrapeconf import ScrapeJob, scrape_config_text

FIXTURES = Path(__file__).parent.parent / "fixtures" / "metrics"
FULL = (FIXTURES / "scrape" / "scrape.yml").read_text(encoding="utf-8")
LINES = FULL.splitlines()
HEADER = LINES[:3]
JOBS = [
    ScrapeJob("sa-home-nodes", "/var/lib/metrics-hub/targets/nodes.json"),
    ScrapeJob("sa-home-extra", "/var/lib/metrics-hub/targets/extra.json"),
]


@pytest.mark.parametrize(
    ("jobs", "lines"),
    [
        (JOBS, LINES),
        (JOBS[:1], LINES[:7]),
        ([], HEADER),
    ],
    ids=["two-jobs", "one-job", "no-jobs"],
)
def test_scrape_config_text(jobs, lines):
    assert scrape_config_text(jobs, "30s") == "\n".join(lines) + "\n"


def test_scrape_config_matches_fixture_bytes():
    assert scrape_config_text(JOBS, "30s") == FULL


def test_interval_is_json_string():
    assert scrape_config_text([], "15s").splitlines()[1] == '  scrape_interval: "15s"'
