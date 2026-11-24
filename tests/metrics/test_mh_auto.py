from __future__ import annotations

import pytest
from support import slot_factory, spec, table

from sa_home_node.config import NodeConfig, NodeSection
from sa_home_node.metrics.config import MetricsConfig
from sa_home_node.services.supervisor import Supervisor


def node_config(tmp_path, hub: bool) -> NodeConfig:
    return NodeConfig(
        path=tmp_path / "config.toml", node=NodeSection(id="n1"), sections={"metrics": {"hub": hub}}
    )


def hub_flag(config: NodeConfig) -> bool:
    return config.section("metrics", MetricsConfig).hub


def make(tmp_path, hub: bool, *assigned: str):
    services = table(
        spec("svc"),
        spec("auto-on", auto=lambda config: True, assignable=False),
        spec("auto-off", auto=lambda config: False),
        spec("auto-hub", auto=hub_flag, assignable=False),
        spec("plain"),
    )
    slots = {}
    auto = services.auto_names(node_config(tmp_path, hub))
    return services, Supervisor(assigned, services, slot_factory(slots), auto=auto), slots


@pytest.mark.parametrize(("hub", "expected"), [(True, ("auto-on", "auto-hub")), (False, ("auto-on",))])
def test_auto_names_follow_condition(tmp_path, hub, expected):
    services, _, _ = make(tmp_path, hub)
    assert services.auto_names(node_config(tmp_path, hub)) == expected


def test_auto_receives_node_config(tmp_path):
    seen = []
    services = table(spec("s", auto=lambda config: seen.append(config) or True))
    config = node_config(tmp_path, False)
    assert services.auto_names(config) == ("s",)
    assert seen == [config]


@pytest.mark.parametrize(("hub", "expected"), [(True, ["auto-on", "auto-hub"]), (False, ["auto-on"])])
async def test_auto_services_start_without_assignment(tmp_path, hub, expected):
    _, supervisor, slots = make(tmp_path, hub)
    assert list(supervisor.services) == expected
    await supervisor.start_all()
    assert {name: slot.trace for name, slot in slots.items()} == {name: ["start"] for name in expected}


async def test_auto_service_is_a_regular_slot(tmp_path):
    _, supervisor, slots = make(tmp_path, True, "svc")
    assert list(supervisor.services) == ["svc", "auto-on", "auto-hub"]
    await supervisor.start_all()
    await supervisor.unassign("auto-hub")
    assert supervisor.get("auto-hub") is None
    assert slots["auto-hub"].trace == ["start", "stop"]
    await supervisor.stop_all()
    assert slots["auto-on"].trace == ["start", "stop"]


def test_assigned_auto_service_is_not_duplicated(tmp_path):
    _, supervisor, slots = make(tmp_path, True, "auto-on", "svc")
    assert list(supervisor.services) == ["auto-on", "svc", "auto-hub"]
    assert supervisor.get("auto-on") is slots["auto-on"]


def test_assignable_names_exclude_non_assignable_auto(tmp_path):
    services, _, _ = make(tmp_path, True)
    assert services.assignable_names() == ("svc", "auto-off", "plain")
