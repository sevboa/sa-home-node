from __future__ import annotations

import pytest
from mh_scenarios import load_fixture, make_ctx

from sa_home_node.fixups.metrics import metrics_fixups
from sa_home_node.fixups.needs import metrics_hub

CATALOG = load_fixture("catalog.json")
CONFIG = CATALOG["config"]


def _hub(tmp_path, hub):
    return make_ctx(tmp_path, {**CONFIG, "hub": hub})


def test_catalog_matches_fixture(tmp_path):
    fixups = metrics_fixups(_hub(tmp_path, True))
    assert [{"id": f.id, "title": f.title, "platforms": list(f.platforms)} for f in fixups] == CATALOG["hub"]


@pytest.mark.parametrize(("hub", "key"), [(True, "hub_needed"), (False, "no_hub_needed")])
def test_needed_follows_hub_flag(tmp_path, hub, key):
    ctx = _hub(tmp_path, hub)
    assert metrics_hub(ctx) is hub
    fixups = metrics_fixups(make_ctx(tmp_path, {**CONFIG, "hub": True}))
    assert [fixup.needed(ctx) for fixup in fixups] == CATALOG[key]


def test_no_fixups_without_hub(tmp_path):
    assert metrics_fixups(_hub(tmp_path, False)) == []
