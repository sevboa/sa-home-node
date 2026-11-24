from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sa_home_node.monitor.models import SmartAttrChange, SmartSnapshot
from sa_home_node.monitor.smartdiff import MONITORED_SMART_ATTRS, diff_smart

T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
T1 = datetime(2026, 1, 1, 11, 0, tzinfo=UTC)


def snap(health, attrs, at=T0):
    return SmartSnapshot("disk:x", "Disk X", health, attrs, at)


def test_monitored_attrs():
    assert set(MONITORED_SMART_ATTRS) == {5, 197, 198, 199}


def test_first_snapshot_is_not_event():
    assert diff_smart(None, snap("ok", {5: 9})) is None


@pytest.mark.parametrize(
    ("prev", "curr"),
    [
        (("ok", {5: 1}), ("ok", {5: 1})),
        (("ok", {5: 1}), ("ok", {})),
        (("ok", {}), ("ok", {5: 1})),
        (("ok", {9: 1}), ("ok", {9: 5})),
        (("ok", {}), ("ok", {})),
        ((None, {}), ("failed", {})),
        (("ok", {}), (None, {})),
    ],
)
def test_no_change(prev, curr):
    assert diff_smart(snap(*prev), snap(*curr, T1)) is None


@pytest.mark.parametrize(
    ("prev", "curr", "event", "attrs"),
    [
        (("ok", {5: 1}), ("ok", {5: 3}), "smart_degraded", ((5, 1, 3),)),
        (("ok", {197: 4}), ("ok", {197: 0}), "smart_recovered", ((197, 4, 0),)),
        (("ok", {}), ("warning", {}), "smart_degraded", ()),
        (("failed", {}), ("ok", {}), "smart_recovered", ()),
        (("ok", {5: 1, 198: 2}), ("ok", {5: 4, 198: 0}), "smart_degraded", ((5, 1, 4), (198, 2, 0))),
        (("warning", {197: 3}), ("ok", {197: 3}), "smart_recovered", ()),
        (("ok", {199: 1}), ("failed", {199: 0}), "smart_degraded", ((199, 1, 0),)),
    ],
)
def test_change(prev, curr, event, attrs):
    res = diff_smart(snap(*prev), snap(*curr, T1))
    assert res is not None
    assert res.event_type == event
    assert (res.component_id, res.label, res.at) == ("disk:x", "Disk X", T1)
    assert (res.health_from, res.health_to) == (prev[0], curr[0])
    assert res.attr_changes == tuple(
        SmartAttrChange(i, MONITORED_SMART_ATTRS[i], o, n) for i, o, n in attrs
    )
