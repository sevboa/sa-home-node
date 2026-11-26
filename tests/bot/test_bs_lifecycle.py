from __future__ import annotations

from types import SimpleNamespace

import pytest
from botfix import load
from drivers_w2b import drive_lifecycle

from sa_home_node.bot import lifecycle, monitor_parse
from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.subs import Subscription

DATA = load("render/lifecycle.json")
NS = SimpleNamespace(Book=SubscriptionBook, Subscription=Subscription, parse=monitor_parse)


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
def test_lifecycle_matches_prototype(case):
    assert drive_lifecycle(case, lifecycle, NS) == case["expected"]
