from __future__ import annotations

import json

from sa_home_node.metrics.names import LABEL_NODE
from sa_home_node.metrics.targets import ScrapeTarget


def file_sd_text(targets: list[ScrapeTarget], port: int) -> str:
    groups = [
        {
            "targets": [f"{target.host}:{port}"],
            "labels": {LABEL_NODE: target.node},
        }
        for target in targets
    ]
    return json.dumps(groups, ensure_ascii=False, indent=2) + "\n"
