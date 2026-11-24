from __future__ import annotations

import pytest
from botfix import drive_linkwatch, load

from sa_home_node.bot import linkwatch
from sa_home_node.bot.linkwatch import LinkWatchMiddleware

DATA = load("linkwatch/scenarios.json")


def make(on_reconnect, threshold, clock):
    extra = {} if threshold is None else {"threshold_seconds": threshold}
    return LinkWatchMiddleware(on_reconnect, clock=clock, **extra)


def test_default_threshold():
    assert linkwatch.OFFLINE_THRESHOLD_S == DATA["default_threshold"]


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_reconnect_matches_prototype(case):
    got = await drive_linkwatch(case, make)
    assert got == {key: case[key] for key in got}
