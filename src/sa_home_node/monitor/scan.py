from __future__ import annotations

from datetime import datetime

from sa_home_node.monitor.config import SensorsConfig
from sa_home_node.monitor.health import compute_health_diff
from sa_home_node.monitor.jobbase import JobContext, JobResult
from sa_home_node.monitor.models import (
    EVENT_OVERHEAT_CLEARED,
    EVENT_OVERHEAT_STARTED,
    KIND_CPU,
    KIND_GPU,
    BaselineStats,
    ComponentPolicy,
    Event,
    HealthState,
    SensorReading,
)
from sa_home_node.monitor.policy import BaselinePolicy, FixedThresholdPolicy
from sa_home_node.monitor.scanhost import scan_host
from sa_home_node.services.slot import utc_now

DEDUP_KEY = "sensor-scan"
JOB_TYPE = "sensor_scan"


def _cfg_for_kind(config: SensorsConfig, kind: str):
    if kind == KIND_CPU:
        return config.cpu
    if kind == KIND_GPU:
        return config.gpu
    return config.disks


def _build_resolver(config: SensorsConfig, stats: dict[str, BaselineStats]):
    def resolve(reading: SensorReading) -> ComponentPolicy:
        cfg = _cfg_for_kind(config, reading.kind)
        if cfg.mode == "baseline":
            policy = BaselinePolicy(
                warn_c=cfg.warn_c,
                crit_c=cfg.crit_c,
                hysteresis_delta_c=cfg.hysteresis_delta_c,
                stats=stats.get(reading.component_id, BaselineStats(0, 0.0, 0.0)),
                min_samples=cfg.baseline_min_samples,
                k_sigma=cfg.baseline_k_sigma,
                min_std_c=cfg.baseline_min_std_c,
            )
        else:
            policy = FixedThresholdPolicy(cfg.warn_c, cfg.crit_c, cfg.hysteresis_delta_c)
        return ComponentPolicy(
            policy=policy,
            consecutive_to_alert=cfg.consecutive_to_alert,
            consecutive_to_clear=cfg.consecutive_to_clear,
        )

    return resolve


def _event_from_state(state: HealthState, event_type: str, now: datetime) -> Event:
    use_alert_time = event_type == EVENT_OVERHEAT_STARTED and state.alerting_since is not None
    return Event(
        type=event_type,
        component_id=state.component_id,
        kind=state.kind,
        label=state.label,
        temperature_c=state.temperature_c,
        at=state.alerting_since if use_alert_time else now,
    )


async def _dispatch_alerts(ctx: JobContext, now: datetime) -> int:
    sent = 0
    for state in await ctx.store.pending_alerts():
        result = await ctx.dispatcher.dispatch_alert(_event_from_state(state, EVENT_OVERHEAT_STARTED, now))
        if result.handled:
            await ctx.store.mark_alert_notified(state.component_id, now)
        sent += bool(result.delivered)
    return sent


async def _dispatch_clears(ctx: JobContext, now: datetime) -> int:
    sent = 0
    for state in await ctx.store.pending_clears():
        result = await ctx.dispatcher.dispatch_clear(_event_from_state(state, EVENT_OVERHEAT_CLEARED, now))
        if result.handled:
            await ctx.store.mark_cleared_notified(state.component_id, now)
        sent += bool(result.delivered)
    return sent


async def _refresh_disk_summaries(ctx: JobContext) -> None:
    if not ctx.config.disks.enabled:
        await ctx.store.save_disk_summaries([])
        return
    health_overrides = await ctx.store.get_smart_health_map()
    await ctx.store.save_disk_summaries(await ctx.sensors.read_disk_summaries(health_overrides))


class SensorScanJob:
    @property
    def dedup_key(self) -> str:
        return DEDUP_KEY

    @property
    def job_type(self) -> str:
        return JOB_TYPE

    async def run(self, ctx: JobContext) -> JobResult:
        now = utc_now()
        readings = await ctx.sensors.read_all()
        config = ctx.config
        uses_baseline = "baseline" in (config.cpu.mode, config.gpu.mode, config.disks.mode)

        stats: dict[str, BaselineStats] = {}
        if uses_baseline:
            for reading in readings:
                cfg = _cfg_for_kind(config, reading.kind)
                if cfg.mode == "baseline":
                    stats[reading.component_id] = await ctx.store.baseline_stats(
                        reading.component_id, cfg.baseline_window
                    )

        diff = compute_health_diff(
            readings, await ctx.store.get_known_states(), _build_resolver(config, stats), now
        )
        await ctx.store.apply_diff(diff, now)
        if uses_baseline:
            await ctx.store.record_readings(readings)

        alerts_sent = await _dispatch_alerts(ctx, now)
        clears_sent = await _dispatch_clears(ctx, now)
        host_scanned, host_transitions, host_alerts, host_clears = await scan_host(ctx, now)
        await _refresh_disk_summaries(ctx)

        return JobResult(
            components_scanned=len(readings) + host_scanned,
            transitions=len(diff.transitions) + host_transitions,
            alerts_sent=alerts_sent + host_alerts,
            clears_sent=clears_sent + host_clears,
        )
