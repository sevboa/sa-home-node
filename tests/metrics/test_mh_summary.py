from __future__ import annotations

import json
from pathlib import Path

from sa_home_node.metrics.summary import summary_table

FIXTURES = Path(__file__).parent.parent / "fixtures" / "metrics"


def test_summary_table_matches_fixture():
    expected = json.loads((FIXTURES / "dashboards" / "summary-table.json").read_text(encoding="utf-8"))
    assert summary_table() == expected


def test_summary_table_is_fresh_each_call():
    first = summary_table()
    first["targets"].clear()
    assert summary_table()["targets"]
