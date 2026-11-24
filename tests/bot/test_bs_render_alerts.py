from __future__ import annotations

import pytest
from botfix import load
from drivers_w2b import drive_alerts

from sa_home_node.bot import monitor_parse, render_alerts

DATA = load("render/alerts.json")


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
def test_alert_text_matches_prototype(case):
    assert drive_alerts(case, render_alerts, monitor_parse) == case["expected"]
