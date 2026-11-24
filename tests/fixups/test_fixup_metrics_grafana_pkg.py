from __future__ import annotations

import pytest
from mh_scenarios import by_id, load_fixture, play, scenario_params

from sa_home_node.fixups.metrics_grafana_pkg import grafana_pkg_fixups

RUNS = load_fixture("runs-grafana-pkg.json")


def test_ids_and_titles_follow_prototype():
    assert [(f.id, f.title) for f in grafana_pkg_fixups()] == [(g["id"], g["title"]) for g in RUNS["fixups"]]


@pytest.mark.parametrize(("fixup_id", "scenario"), scenario_params(RUNS))
def test_scenario(root, monkeypatch, fixup_id, scenario):
    result = play(by_id(grafana_pkg_fixups(), fixup_id), root, scenario["setup"], monkeypatch)
    assert result == scenario["expect"]
