from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sa_home_node.monitor.hostmodels import (
    EVENT_HOST_DEGRADED,
    EVENT_HOST_RECOVERED,
    HostEvent,
    HostMetricPolicy,
    HostMetricReading,
    HostMetricState,
    HostPolicyResolver,
)
from sa_home_node.monitor.metrics import compute_host_diff, metric_spec
from sa_home_node.monitor.sensors.hostscan import KERNEL_SCAN_STATE_KEY

if TYPE_CHECKING:
    from sa_home_node.monitor.config import SensorsConfig
    from sa_home_node.monitor.jobbase import JobContext


def host_resolver(config: SensorsConfig) -> HostPolicyResolver:
    host_cfg = config.host

    def resolve(reading: HostMetricReading) -> HostMetricPolicy:
        spec = metric_spec(reading.metric)
        if not host_cfg.enabled:
            spec = spec.without_thresholds()
        override = host_cfg.thresholds.get(reading.metric)
        if override is not None and spec.warn is not None:
            spec = spec.with_thresholds(override.warn, override.crit)
        return HostMetricPolicy(
            spec=spec,
            consecutive_to_alert=host_cfg.consecutive_to_alert,
            consecutive_to_clear=host_cfg.consecutive_to_clear,
        )

    return resolve


def _host_event(state: HostMetricState, event_type: str, now: datetime) -> HostEvent:
    spec = metric_spec(state.metric)
    use_alert_time = event_type == EVENT_HOST_DEGRADED and state.alerting_since is not None
    return HostEvent(
        type=event_type,
        component_id=state.component_id,
        metric=state.metric,
        label=state.label,
        value=state.value,
        unit=state.unit,
        hint=spec.hint if spec else "",
        at=state.alerting_since if use_alert_time else now,
    )


async def _kernel_since(ctx: JobContext) -> datetime | None:
    raw = await ctx.store.get_state(KERNEL_SCAN_STATE_KEY)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


async def scan_host(ctx: JobContext, now: datetime) -> tuple[int, int, int, int]:
    export = ctx.metrics.export
    readings = await ctx.sensors.read_host(kernel_since=await _kernel_since(ctx), export=export)
    if not readings:
        return 0, 0, 0, 0
    await ctx.store.set_state(KERNEL_SCAN_STATE_KEY, now.isoformat())
    known = await ctx.store.get_known_host_states()
    diff = compute_host_diff(readings, known, host_resolver(ctx.config), now)
    await ctx.store.apply_host_diff(diff, now)
    await ctx.store.record_host_readings(readings)

    alerts = 0
    for state in await ctx.store.pending_host_alerts():
        result = await ctx.dispatcher.dispatch_host_alert(_host_event(state, EVENT_HOST_DEGRADED, now))
        if result.handled:
            await ctx.store.mark_host_alert_notified(state.component_id, now)
        alerts += result.delivered
    clears = 0
    for state in await ctx.store.pending_host_clears():
        result = await ctx.dispatcher.dispatch_host_clear(_host_event(state, EVENT_HOST_RECOVERED, now))
        if result.handled:
            await ctx.store.mark_host_cleared_notified(state.component_id, now)
        clears += result.delivered
    return len(readings), len(diff.transitions), alerts, clears
