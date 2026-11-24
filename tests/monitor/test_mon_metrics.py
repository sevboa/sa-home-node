from __future__ import annotations

from datetime import UTC, datetime

from sa_home_node.monitor.hostmodels import (
    DIR_ABOVE,
    DIR_BELOW,
    HostMetricPolicy,
    HostMetricReading,
)
from sa_home_node.monitor.metrics import METRICS, compute_host_diff
from sa_home_node.monitor.models import ALERTING, OK, KnownState

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

TABLE = {
    "steal_pct": ("CPU steal", "%", DIR_ABOVE, 10.0, 25.0, 3.0),
    "iowait_pct": ("iowait", "%", DIR_ABOVE, 15.0, 40.0, 4.0),
    "load_per_core": ("load/ядро", "", DIR_ABOVE, 1.5, 4.0, 0.4),
    "mem_available_pct": ("RAM свободно", "%", DIR_BELOW, 15.0, 7.0, 4.0),
    "swap_used_pct": ("swap занято", "%", DIR_ABOVE, 25.0, 60.0, 6.0),
    "disk_used_pct": ("диск / заполнен", "%", DIR_ABOVE, 80.0, 92.0, 3.0),
    "psi_cpu": ("PSI cpu", "%", DIR_ABOVE, 20.0, 50.0, 5.0),
    "psi_memory": ("PSI mem", "%", DIR_ABOVE, 20.0, 50.0, 5.0),
    "psi_io": ("PSI io", "%", DIR_ABOVE, 20.0, 50.0, 5.0),
    "oom_kills": ("OOM-kill", "/мин", DIR_ABOVE, 1.0, 1.0, 1.0),
    "conntrack_pct": ("conntrack", "%", DIR_ABOVE, 80.0, 95.0, 3.0),
    "net_err_rate": ("ошибки NIC", "/мин", DIR_ABOVE, 1.0, 10.0, 1.0),
    "kernel_stall_events": ("стойла ядра", "/скан", DIR_ABOVE, 1.0, 1.0, 1.0),
}


def test_table_complete() -> None:
    assert list(METRICS) == list(TABLE)
    for name, (label, unit, direction, warn, crit, hyst) in TABLE.items():
        spec = METRICS[name]
        assert (spec.metric, spec.label, spec.unit, spec.direction) == (name, label, unit, direction)
        assert (spec.warn, spec.crit, spec.hysteresis) == (warn, crit, hyst)
        assert spec.hint


def reading(value: float, metric: str = "steal_pct") -> HostMetricReading:
    return HostMetricReading(f"host:{metric}", metric, "x", value, "%", NOW)


def resolver(alert: int = 2, clear: int = 2):
    return lambda r: HostMetricPolicy(METRICS[r.metric], alert, clear)


def test_diff_alert_after_consecutive() -> None:
    first = compute_host_diff([reading(50)], {}, resolver(), NOW)
    assert first.transitions == []
    state = first.states[0]
    assert (state.status, state.consecutive_count) == (OK, 1)
    known = {state.component_id: KnownState(state.component_id, state.status, state.consecutive_count, None)}
    second = compute_host_diff([reading(50)], known, resolver(), NOW)
    assert second.states[0].status == ALERTING
    assert second.states[0].alerting_since == NOW
    transition = second.transitions[0]
    assert (transition.from_status, transition.to_status) == (OK, ALERTING)
    assert transition.hint == METRICS["steal_pct"].hint


def test_diff_clear_and_mid() -> None:
    since = datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
    known = {"host:steal_pct": KnownState("host:steal_pct", ALERTING, 1, since)}
    mid = compute_host_diff([reading(8)], known, resolver(), NOW)
    assert (mid.states[0].status, mid.states[0].consecutive_count) == (ALERTING, 0)
    assert mid.states[0].alerting_since == since
    assert mid.transitions == []
    cleared = compute_host_diff([reading(1)], known, resolver(), NOW)
    assert cleared.states[0].status == OK
    assert cleared.transitions[0].to_status == OK
    assert cleared.states[0].alerting_since is None


def test_diff_ignores_missing_known() -> None:
    known = {"host:gone": KnownState("host:gone", ALERTING, 0, NOW)}
    diff = compute_host_diff([reading(1)], known, resolver(), NOW)
    assert [s.component_id for s in diff.states] == ["host:steal_pct"]
