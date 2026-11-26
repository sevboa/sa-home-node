from __future__ import annotations

import pytest
from botfix import load
from drivers_w2b import drive_status

from sa_home_node.bot import monitor_parse, render_status

DATA = load("render/status.json")


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
def test_status_text_matches_prototype(case):
    assert drive_status(case, render_status, monitor_parse) == case["expected"]
