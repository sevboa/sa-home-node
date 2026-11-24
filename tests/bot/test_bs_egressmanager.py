from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.client.session.aiohttp import AiohttpSession
from drivers_w3d import (
    drive_manager,
    drive_notices,
    drive_pick,
    drive_startup,
    drive_state_file,
    load_shared,
)

from sa_home_node.bot import egress, egressmanager, retry
from sa_home_node.bot.book import SubscriptionBook
from sa_home_node.bot.config import TelegramConfig
from sa_home_node.bot.subs import Subscription

DATA = load_shared("egress/manager.json")


def make(session, cfg, fetch, probe, on_switched, path, clock, on_recovered):
    return egressmanager.EgressManager(
        session=session,
        config=cfg,
        fetch_candidates=fetch,
        probe_direct=probe,
        on_switched=on_switched,
        state_path=path,
        clock=clock,
        on_recovered=on_recovered,
    )


def cfg_of(**kwargs):
    return TelegramConfig(**kwargs)


MANAGER = SimpleNamespace(
    session=AiohttpSession, apply=egress.apply_route, current=egress.current_route, cfg=cfg_of, make=make
)
FILES = SimpleNamespace(load=egressmanager.load_saved_state, save=egressmanager.save_state)
STARTUP = SimpleNamespace(cfg=cfg_of, startup=egressmanager.startup_routes)
PICK = SimpleNamespace(
    session=AiohttpSession, current=egress.current_route, pick=egressmanager.pick_startup_route
)
NOTICES = SimpleNamespace(
    Subscription=Subscription,
    Book=SubscriptionBook,
    notices=lambda book, sink, flusher, clock: egressmanager.SwitchNotices(book, sink, flusher, clock=clock),
)


def test_request_timeout_is_shared():
    assert retry.REQUEST_TIMEOUT_S == DATA["request_timeout"]


@pytest.mark.parametrize("case", DATA["manager"], ids=lambda c: c["name"])
async def test_manager_matches_prototype(case, tmp_path):
    assert await drive_manager(case, MANAGER, tmp_path) == case["expected"]


@pytest.mark.parametrize("case", DATA["state_files"], ids=lambda c: c["name"])
def test_state_files_match_prototype(case, tmp_path):
    assert drive_state_file(case, FILES, tmp_path) == case["expected"]


@pytest.mark.parametrize("case", DATA["startup"], ids=lambda c: c["name"])
def test_startup_routes_match_prototype(case, tmp_path):
    assert drive_startup(case, STARTUP, tmp_path) == case["expected"]


@pytest.mark.parametrize("case", DATA["pick"], ids=lambda c: c["name"])
async def test_pick_startup_route_matches_prototype(case):
    assert await drive_pick(case, PICK) == case["expected"]


@pytest.mark.parametrize("case", DATA["render_switch"], ids=lambda c: c["name"])
def test_render_switch_matches_prototype(case):
    assert egressmanager.render_switch(*case["args"]) == case["expected"]


@pytest.mark.parametrize("case", DATA["notices"], ids=lambda c: c["name"])
async def test_switch_notices_match_prototype(case):
    assert await drive_notices(case, NOTICES) == case["expected"]
