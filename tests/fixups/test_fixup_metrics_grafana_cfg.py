from __future__ import annotations

import pytest
from mh_scenarios import load_fixture, make_ctx, play, scenario_params

from sa_home_node.fixups.core import FixupError
from sa_home_node.fixups.metrics_grafana_cfg import grafana_config_fixup

RUNS = load_fixture("runs-grafana-cfg.json")
GROUP = RUNS["fixups"][0]


def test_id_and_title_follow_prototype(tmp_path):
    fixup = grafana_config_fixup(make_ctx(tmp_path, RUNS["config"]))
    assert (fixup.id, fixup.title) == (GROUP["id"], GROUP["title"])


@pytest.mark.parametrize(("fixup_id", "scenario"), scenario_params(RUNS))
def test_scenario(tmp_path, root, monkeypatch, fixup_id, scenario):
    fixup = grafana_config_fixup(make_ctx(tmp_path, RUNS["config"]))
    assert play(fixup, root, scenario["setup"], monkeypatch) == scenario["expect"]


def test_apply_without_address_is_refused(tmp_path, root, monkeypatch):
    scenario = next(s for s in GROUP["scenarios"] if s["name"] == "no-address")
    fixup = grafana_config_fixup(make_ctx(tmp_path, RUNS["config"]))
    play(fixup, root, scenario["setup"], monkeypatch)
    root.calls.clear()
    with pytest.raises(FixupError, match="адреса оверлейной сети"):
        fixup.apply()
    assert root.sudo_calls == []
