from __future__ import annotations

from types import SimpleNamespace

import pytest
from botfix import load
from drivers_w2a import drive_scan

from sa_home_node.bot import scanlimit
from sa_home_node.bot.db import AppState, open_bot_db

DATA = load("status/scan-limit.json")
PREFIX = "action_ticks:"


async def open_scan(path):
    db = await open_bot_db(path, ())
    return AppState(db), db.close


KIT = SimpleNamespace(
    open=open_scan,
    decide=scanlimit.decide,
    get=scanlimit.get_action_ticks,
    put=scanlimit.set_action_ticks,
    raw=lambda state, key: state.get(PREFIX + key),
    put_raw=lambda state, key, value: state.set(PREFIX + key, value),
)


def test_constants():
    assert scanlimit.MIN_INTERVAL.total_seconds() == DATA["min_interval_s"]
    assert scanlimit.MAX_PER_DAY == DATA["max_per_day"]
    assert scanlimit.WINDOW.total_seconds() == DATA["window_s"]


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
async def test_scan_limit_matches_prototype(case, tmp_path):
    got = await drive_scan(case, KIT, tmp_path)
    assert got == {key: case[key] for key in got}
