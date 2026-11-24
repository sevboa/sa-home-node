from __future__ import annotations

from types import SimpleNamespace

import pytest
from botfix import load
from drivers_w2b import drive_guests

from sa_home_node.bot import guestsview
from sa_home_node.bot.rights import GuestRight, RightGroup, RightsCatalog
from sa_home_node.bot.subs import Subscription

DATA = load("guests/views.json")


def make_catalog(groups):
    return RightsCatalog(
        [
            RightGroup(
                g["key"],
                g["title"],
                tuple(GuestRight(m["right"], m["title"], m["hint"]) for m in g["members"]),
                g["fine"],
            )
            for g in groups
        ]
    )


CATALOGS = {name: make_catalog(groups) for name, groups in DATA["catalogs"].items()}
NS = SimpleNamespace(Subscription=Subscription, catalog_args=lambda name: (CATALOGS[name],))


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
def test_guest_views_match_prototype(case):
    assert drive_guests(case, guestsview, NS) == case["expected"]
