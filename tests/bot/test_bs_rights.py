from __future__ import annotations

import pytest
from botfix import load

from sa_home_node.bot.rights import GuestRight, RightGroup, RightsCatalog

DATA = load("rights/catalog.json")


def make_group(spec: dict) -> RightGroup:
    members = tuple(GuestRight(m["right"], m["title"], m["hint"]) for m in spec["members"])
    return RightGroup(spec["key"], spec["title"], members, spec["fine"])


CATALOG = RightsCatalog([make_group(spec) for spec in DATA["catalog"]])


@pytest.mark.parametrize("right", sorted(DATA["labels"]))
def test_label(right):
    assert CATALOG.label(right) == DATA["labels"][right]


@pytest.mark.parametrize("key", sorted(DATA["lookups"]))
def test_group_lookup(key):
    group = CATALOG.group(key)
    assert (group.title if group else None) == DATA["lookups"][key]
    if group:
        assert group.rights() == frozenset(DATA["group_rights"][key])


def test_grantable_excludes_fine_and_wildcard():
    assert set(CATALOG.grantable()) == set(DATA["grantable"])
    wild = RightGroup("all", "все", (GuestRight("*", "всё", ""), GuestRight("x@y", "икс", "")))
    assert set(RightsCatalog([wild]).grantable()) == {"x@y"}


@pytest.mark.parametrize(
    "case", DATA["ops"], ids=lambda c: f"{c['group']}-{len(c['current'])}-{'-'.join(c['current'])}"
)
def test_group_on_off(case):
    group = CATALOG.group(case["group"])
    current = frozenset(case["current"])
    assert sorted(CATALOG.apply_group_on(current, group)) == case["on"]
    assert sorted(CATALOG.apply_group_off(current, group)) == case["off"]
    assert isinstance(CATALOG.apply_group_on(current, group), frozenset)
    assert current == frozenset(case["current"])
