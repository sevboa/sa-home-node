from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sa_home_node.monitor.health import compute_health_diff, reconcile_band
from sa_home_node.monitor.models import (
    ALERTING,
    BAND_MID,
    BAND_OVER,
    BAND_UNDER,
    KIND_CPU,
    OK,
    ComponentPolicy,
    KnownState,
    SensorReading,
)

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)
EARLIER = datetime(2026, 1, 1, 11, tzinfo=UTC)


def run(bands, *, to_alert=3, to_clear=2, status=OK, count=0, since=None):
    out = []
    for band in bands:
        res = reconcile_band(
            band,
            prev_status=status,
            prev_count=count,
            alerting_since=since,
            consecutive_to_alert=to_alert,
            consecutive_to_clear=to_clear,
            now=NOW,
        )
        status, count, since = res.status, res.consecutive_count, res.alerting_since
        out.append((res.status, res.consecutive_count, res.transitioned_to))
    return out


@pytest.mark.parametrize(
    ("bands", "kwargs", "expected"),
    [
        (
            [BAND_OVER, BAND_OVER, BAND_OVER],
            {},
            [(OK, 1, None), (OK, 2, None), (ALERTING, 0, ALERTING)],
        ),
        (
            [BAND_OVER, BAND_UNDER, BAND_OVER, BAND_OVER],
            {},
            [(OK, 1, None), (OK, 0, None), (OK, 1, None), (OK, 2, None)],
        ),
        (
            [BAND_OVER, BAND_MID, BAND_OVER],
            {"to_alert": 2},
            [(OK, 1, None), (OK, 0, None), (OK, 1, None)],
        ),
        (
            [BAND_UNDER, BAND_UNDER],
            {"status": ALERTING, "since": EARLIER},
            [(ALERTING, 1, None), (OK, 0, OK)],
        ),
        (
            [BAND_UNDER, BAND_MID, BAND_UNDER, BAND_OVER],
            {"status": ALERTING, "since": EARLIER},
            [(ALERTING, 1, None), (ALERTING, 0, None), (ALERTING, 1, None), (ALERTING, 0, None)],
        ),
    ],
)
def test_reconcile_sequences(bands, kwargs, expected):
    assert run(bands, **kwargs) == expected


def test_reconcile_keeps_and_resets_alerting_since():
    kept = reconcile_band(
        BAND_OVER,
        prev_status=ALERTING,
        prev_count=1,
        alerting_since=EARLIER,
        consecutive_to_alert=1,
        consecutive_to_clear=2,
        now=NOW,
    )
    assert kept.alerting_since == EARLIER
    entered = reconcile_band(
        BAND_OVER,
        prev_status=OK,
        prev_count=0,
        alerting_since=None,
        consecutive_to_alert=1,
        consecutive_to_clear=2,
        now=NOW,
    )
    assert entered.alerting_since == NOW
    cleared = reconcile_band(
        BAND_UNDER,
        prev_status=ALERTING,
        prev_count=0,
        alerting_since=EARLIER,
        consecutive_to_alert=1,
        consecutive_to_clear=1,
        now=NOW,
    )
    assert cleared.alerting_since is None


class Fixed:
    def band(self, reading):
        return BAND_OVER if reading.temperature_c >= 80 else BAND_UNDER


def reading(cid, temp):
    return SensorReading(cid, KIND_CPU, cid.upper(), temp, NOW)


def resolve(_reading):
    return ComponentPolicy(Fixed(), 2, 2)


def test_diff_flapping_and_transition():
    known = {"a": KnownState("a", OK, 1, None)}
    diff = compute_health_diff([reading("a", 90), reading("b", 50)], known, resolve, NOW)
    assert [(s.component_id, s.status, s.consecutive_count) for s in diff.states] == [
        ("a", ALERTING, 0),
        ("b", OK, 0),
    ]
    assert len(diff.transitions) == 1
    t = diff.transitions[0]
    assert (t.component_id, t.from_status, t.to_status, t.temperature_c, t.at) == (
        "a",
        OK,
        ALERTING,
        90,
        NOW,
    )


def test_diff_clear_transition_and_unknown_component_starts_ok():
    known = {"a": KnownState("a", ALERTING, 1, EARLIER)}
    diff = compute_health_diff([reading("a", 40), reading("new", 90)], known, resolve, NOW)
    assert [(t.component_id, t.from_status, t.to_status) for t in diff.transitions] == [
        ("a", ALERTING, OK)
    ]
    assert diff.states[1].status == OK and diff.states[1].consecutive_count == 1


def test_diff_missing_component_not_reported():
    known = {"gone": KnownState("gone", ALERTING, 0, EARLIER)}
    diff = compute_health_diff([reading("a", 50)], known, resolve, NOW)
    assert [s.component_id for s in diff.states] == ["a"]
    assert diff.transitions == []
    assert compute_health_diff([], known, resolve, NOW).states == []
