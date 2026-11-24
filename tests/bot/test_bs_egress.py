from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.client.session.aiohttp import AiohttpSession
from botfix import load
from drivers_w2a import drive_route, drive_trouble

from sa_home_node.bot import egress
from sa_home_node.bot.egress import (
    Candidate,
    Decision,
    TroubleDetector,
    apply_route,
    choose_route,
    current_route,
    normalize_candidates,
    socks_url,
)

DATA = load("egress/decisions.json")
CHOICES = DATA["choose"]
KIT = SimpleNamespace(session=AiohttpSession, apply=apply_route, current=current_route, direct=egress.DIRECT)


@pytest.mark.parametrize("name", sorted(DATA["constants"]))
def test_constants(name):
    assert getattr(egress, name) == DATA["constants"][name]


def test_fresh_session_goes_direct():
    assert current_route(AiohttpSession()) == DATA["fresh_route"]


@pytest.mark.parametrize("pair", DATA["socks_url"], ids=lambda p: repr(p[0]))
def test_socks_url(pair):
    assert socks_url(pair[0]) == pair[1]


def test_value_types():
    assert Candidate("r", "l", 5, True) == Candidate(route="r", label="l", ms=5, valid=True)
    assert Decision("r", "why", False) == Decision(route="r", reason="why", changed=False)


@pytest.mark.parametrize("case", DATA["normalize"], ids=lambda c: c["name"])
def test_normalize_candidates(case):
    got = normalize_candidates(case["raw"], case["extra"])
    assert [[c.route, c.label, c.ms, c.valid] for c in got] == case["out"]


@pytest.mark.parametrize("case", CHOICES, ids=[str(index) for index in range(len(CHOICES))])
def test_choose_route(case):
    kwargs = {key: value for key, value in case.items() if key not in ("set", "out")}
    decision = choose_route(**kwargs)
    assert [decision.route, decision.reason, decision.changed] == case["out"]


@pytest.mark.parametrize("case", DATA["trouble"], ids=lambda c: c["name"])
async def test_trouble_detector(case):
    got = await drive_trouble(
        case, lambda on_trouble, on_recovered, clock: TroubleDetector(on_trouble, on_recovered, clock)
    )
    assert got == {key: case[key] for key in got}


@pytest.mark.parametrize("case", DATA["routes"], ids=lambda c: c["name"])
def test_apply_route(case):
    assert drive_route(case, KIT) == case["out"]
