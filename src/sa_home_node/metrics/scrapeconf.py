from __future__ import annotations

import json
from dataclasses import dataclass

from sa_home_node.promexport import METRICS_PATH


@dataclass(frozen=True)
class ScrapeJob:
    name: str
    targets_file: str


def _job_lines(job: ScrapeJob) -> list[str]:
    return [
        f"  - job_name: {json.dumps(job.name)}",
        f"    metrics_path: {json.dumps(METRICS_PATH)}",
        "    file_sd_configs:",
        f"      - files: [{json.dumps(job.targets_file)}]",
    ]


def scrape_config_text(jobs: list[ScrapeJob], interval: str) -> str:
    lines = [
        "global:",
        f"  scrape_interval: {json.dumps(interval)}",
        "scrape_configs:",
    ]
    for job in jobs:
        lines.extend(_job_lines(job))
    return "\n".join(lines) + "\n"
