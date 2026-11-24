from __future__ import annotations

import pytest
from mh_scenarios import load_fixture, make_ctx, play, scenario_params

from sa_home_node.fixups.metrics_provision import grafana_provisioning_fixup

RUNS = load_fixture("runs-provision.json")
GROUP = RUNS["fixups"][0]


def test_id_and_title_follow_prototype(tmp_path):
    fixup = grafana_provisioning_fixup(make_ctx(tmp_path, RUNS["config"]))
    assert (fixup.id, fixup.title) == (GROUP["id"], GROUP["title"])


@pytest.mark.parametrize(("fixup_id", "scenario"), scenario_params(RUNS))
def test_scenario(tmp_path, root, monkeypatch, fixup_id, scenario):
    fixup = grafana_provisioning_fixup(make_ctx(tmp_path, RUNS["config"]))
    assert play(fixup, root, scenario["setup"], monkeypatch) == scenario["expect"]


def test_dashboard_json_is_not_installed(tmp_path, root, monkeypatch):
    fixup = grafana_provisioning_fixup(make_ctx(tmp_path, RUNS["config"]))
    result = play(fixup, root, {}, monkeypatch)
    assert not [path for path in result["installed"] if path.startswith("/var/lib/")]
