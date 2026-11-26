from __future__ import annotations

from datetime import datetime

import pytest
from ctlfix import load, zone

from sa_home_node.ctl.render import render_event, render_status
from sa_home_node.proto.wire import make_event

DATA = load("render.json")


@pytest.mark.parametrize("case", DATA["status"], ids=lambda c: c["state"].get("node", "empty"))
def test_status_text(case):
    with zone(DATA["zone"]):
        assert render_status(case["state"]) == case["text"]


@pytest.mark.parametrize(
    ("name", "data", "text"),
    [
        ("service_started", {"name": "monitor", "pid": 7}, "12:34:56 service_started name=monitor pid=7"),
        ("peer_down", None, "12:34:56 peer_down"),
    ],
)
def test_event_line(name, data, text):
    assert render_event(make_event(name, data), datetime(2026, 1, 2, 12, 34, 56)) == text
