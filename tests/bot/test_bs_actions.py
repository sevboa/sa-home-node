from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.types import InlineKeyboardButton
from botfix import load
from drivers_w2b import drive_actions

from sa_home_node.bot import actions
from sa_home_node.bot.subs import Subscription
from sa_home_node.proto.describe import ActionSpec, ServiceDescription
from sa_home_node.proto.wire import Address, ProtoError, ServiceUnavailableError

DATA = load("actions/cases.json")
NS = SimpleNamespace(
    ActionSpec=ActionSpec,
    ServiceDescription=ServiceDescription,
    ProtoError=ProtoError,
    ServiceUnavailableError=ServiceUnavailableError,
    Address=Address,
    Subscription=Subscription,
    find_action=lambda mod, link, service, action_id, node_id: mod.find_action(
        link, service, action_id, node_id
    ),
    run_action=lambda mod, link, service, action_id, value, node_id: mod.run_action(
        link, service, action_id, value, node_id
    ),
    button=lambda text, data: InlineKeyboardButton(text=text, callback_data=data),
)


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: c["name"])
def test_actions_match_prototype(case):
    assert drive_actions(case, actions, NS) == case["expected"]
