from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from sa_home_node import promexport
from sa_home_node.monitor.hostmodels import HostMetricState
from sa_home_node.monitor.metrics import instance_of, metric_spec
from sa_home_node.monitor.models import HealthState
from sa_home_node.promexport import METRIC_NAME, Render, gauge_family, sample

if TYPE_CHECKING:
    from sa_home_node.monitor.store import MonitorStore


def render_metrics(health: Iterable[HealthState], host_states: Iterable[HostMetricState]) -> str:
    lines = gauge_family(promexport.METRIC_UP, "служба monitor жива", [sample(promexport.METRIC_UP, {}, 1)])
    by_metric: dict[str, list[HostMetricState]] = {}
    for state in host_states:
        if METRIC_NAME.match(state.metric):
            by_metric.setdefault(state.metric, []).append(state)
    for metric in sorted(by_metric):
        name = promexport.HOST_METRIC_PREFIX + metric
        spec = metric_spec(metric)
        title = f"{spec.label}, {spec.unit}".rstrip(", ") if spec else metric
        label_name = spec.instance_label if spec else None
        samples = []
        for state in by_metric[metric]:
            instance = instance_of(state.component_id, metric)
            labels = {label_name: instance} if label_name and instance is not None else {}
            samples.append(sample(name, labels, state.value))
        lines.extend(gauge_family(name, title, samples))
    temperatures = sorted(health, key=lambda s: (s.kind, s.component_id))
    if temperatures:
        lines.extend(
            gauge_family(
                promexport.METRIC_TEMPERATURE,
                "температура компонента, °C",
                (
                    sample(
                        promexport.METRIC_TEMPERATURE,
                        {
                            promexport.LABEL_KIND: state.kind,
                            promexport.LABEL_COMPONENT: state.component_id,
                        },
                        state.temperature_c,
                    )
                    for state in temperatures
                ),
            )
        )
    return "\n".join(lines) + "\n"


def store_renderer(store: MonitorStore) -> Render:
    async def render() -> str:
        return render_metrics(await store.get_all_states(), await store.get_all_host_states())

    return render
