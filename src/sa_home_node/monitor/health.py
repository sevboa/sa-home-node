from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sa_home_node.monitor.models import (
    ALERTING,
    BAND_OVER,
    BAND_UNDER,
    OK,
    ComponentPolicy,
    HealthDiff,
    HealthState,
    KnownState,
    SensorReading,
    Transition,
)

PolicyResolver = Callable[[SensorReading], ComponentPolicy]


@dataclass(frozen=True)
class BandReconcile:
    status: str
    consecutive_count: int
    alerting_since: datetime | None
    transitioned_to: str | None


def reconcile_band(
    band: str,
    *,
    prev_status: str,
    prev_count: int,
    alerting_since: datetime | None,
    consecutive_to_alert: int,
    consecutive_to_clear: int,
    now: datetime,
) -> BandReconcile:
    if prev_status == OK:
        if band == BAND_OVER:
            count = prev_count + 1
            if count >= consecutive_to_alert:
                return BandReconcile(ALERTING, 0, now, ALERTING)
            return BandReconcile(OK, count, None, None)
        return BandReconcile(OK, 0, None, None)

    if band == BAND_UNDER:
        count = prev_count + 1
        if count >= consecutive_to_clear:
            return BandReconcile(OK, 0, None, OK)
        return BandReconcile(ALERTING, count, alerting_since, None)
    return BandReconcile(ALERTING, 0, alerting_since, None)


def _reconcile_one(
    reading: SensorReading,
    known: KnownState | None,
    cpolicy: ComponentPolicy,
    now: datetime,
) -> tuple[HealthState, Transition | None]:
    prev_status = known.status if known else OK
    res = reconcile_band(
        cpolicy.policy.band(reading),
        prev_status=prev_status,
        prev_count=known.consecutive_count if known else 0,
        alerting_since=known.alerting_since if known else None,
        consecutive_to_alert=cpolicy.consecutive_to_alert,
        consecutive_to_clear=cpolicy.consecutive_to_clear,
        now=now,
    )
    state = HealthState(
        component_id=reading.component_id,
        kind=reading.kind,
        label=reading.label,
        status=res.status,
        temperature_c=reading.temperature_c,
        consecutive_count=res.consecutive_count,
        alerting_since=res.alerting_since,
    )
    transition = None
    if res.transitioned_to is not None:
        transition = Transition(
            component_id=reading.component_id,
            kind=reading.kind,
            label=reading.label,
            from_status=prev_status,
            to_status=res.transitioned_to,
            temperature_c=reading.temperature_c,
            at=now,
        )
    return state, transition


def compute_health_diff(
    current: list[SensorReading],
    known: dict[str, KnownState],
    resolve_policy: PolicyResolver,
    now: datetime,
) -> HealthDiff:
    states: list[HealthState] = []
    transitions: list[Transition] = []
    for reading in current:
        state, transition = _reconcile_one(
            reading, known.get(reading.component_id), resolve_policy(reading), now
        )
        states.append(state)
        if transition is not None:
            transitions.append(transition)
    return HealthDiff(states=states, transitions=transitions)
