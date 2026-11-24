from __future__ import annotations

import os

import pytest
from mh_scenarios import by_id, load_fixture, make_ctx, play, scenario_params

from sa_home_node.fixups.metrics_vm import vm_fixups
from sa_home_node.metrics.spec import ScrapeJobSpec
from sa_home_node.plugins import Plugin

RUNS = load_fixture("runs-vm.json")
CONFIG_GROUP = next(g for g in RUNS["fixups"] if g["id"] == "victoria-metrics-config")
RUNS_SCENARIOS = {s["name"]: s for s in CONFIG_GROUP["scenarios"]}
EXTRA = Plugin(
    name="extra", scrape_jobs=(ScrapeJobSpec("sa-home-extra", "extra.json", port=lambda config: 9101),)
)


def test_ids_and_titles_follow_prototype(tmp_path):
    fixups = vm_fixups(make_ctx(tmp_path, RUNS["config"], (EXTRA,)))
    assert [(f.id, f.title) for f in fixups] == [(g["id"], g["title"]) for g in RUNS["fixups"]]


@pytest.mark.parametrize(("fixup_id", "scenario"), scenario_params(RUNS))
def test_scenario(tmp_path, root, monkeypatch, fixup_id, scenario):
    fixups = vm_fixups(make_ctx(tmp_path, RUNS["config"], (EXTRA,)))
    assert play(by_id(fixups, fixup_id), root, scenario["setup"], monkeypatch) == scenario["expect"]


def test_scrape_jobs_come_from_plugins(tmp_path, root, monkeypatch):
    plain = vm_fixups(make_ctx(tmp_path, RUNS["config"]))
    setup = RUNS_SCENARIOS["converged"]["setup"]
    result = play(by_id(plain, "victoria-metrics-config"), root, setup, monkeypatch)
    scrape = result["installed"]["/etc/victoria-metrics/scrape.yml"]
    assert result["check_before"] is False
    assert "sa-home-nodes" in scrape
    assert "sa-home-extra" not in scrape


def test_runtime_dir_converged(tmp_path, root, monkeypatch):
    runtime = tmp_path / "rt"
    for path in (runtime, runtime / "targets", runtime / "dashboards"):
        path.mkdir(parents=True)
        os.chmod(path, 0o755)
    ctx = make_ctx(tmp_path, {**RUNS["config"], "runtime_dir": str(runtime)})
    assert by_id(vm_fixups(ctx), "metrics-runtime-dir").check()
    os.chmod(runtime / "targets", 0o700)
    assert not by_id(vm_fixups(ctx), "metrics-runtime-dir").check()
