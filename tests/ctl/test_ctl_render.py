from __future__ import annotations

import pytest
from ctlfix import load, zone

from sa_home_node.ctl.render import render_check_update, render_services, render_update_result

DATA = load("render.json")


@pytest.mark.parametrize("case", DATA["services"], ids=lambda c: str(len(c["services"])))
def test_services_table(case):
    with zone(DATA["zone"]):
        assert render_services(case["services"]) == case["text"]


@pytest.mark.parametrize(
    ("render", "key"),
    [(render_check_update, "check_update"), (render_update_result, "update")],
)
def test_update_texts(render, key):
    for case in DATA[key]:
        assert render(case["result"]) == case["text"]
