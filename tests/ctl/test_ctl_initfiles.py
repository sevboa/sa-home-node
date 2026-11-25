from __future__ import annotations

import tomllib
from pathlib import Path

from sa_home_node.config import load_config
from sa_home_node.swarm.config import swarm_section
from sa_home_node.wizard.files import NodeAnswers, render_config, render_systemd_unit

ANSWERS = NodeAnswers(
    node_id="n1",
    kind="vps",
    assignments=["monitor", "apps"],
    listen=["tcp://10.0.0.1:8710"],
    token='t"o\\ken',
    join="tcp://10.0.0.2:8710",
)


def test_config_round_trip(tmp_path: Path):
    data_dir = tmp_path / "work"
    path = tmp_path / "config.toml"
    path.write_text(render_config(ANSWERS, data_dir), encoding="utf-8")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert set(raw) == {"node", "swarm"}
    cfg = load_config(path)
    assert cfg.node.id == "n1"
    assert cfg.node.kind == "vps"
    assert cfg.node.assignments == ["monitor", "apps"]
    assert cfg.node.listen == ["tcp://10.0.0.1:8710"]
    assert cfg.node.data_dir == data_dir / "data"
    assert Path(cfg.node.socket) == data_dir / "data" / "node.sock"
    assert Path(cfg.node.state_path) == data_dir / "data" / "node-state.json"
    swarm = swarm_section(cfg)
    assert swarm.token == ANSWERS.token
    assert swarm.join == ANSWERS.join


def test_config_empty_lists(tmp_path: Path):
    answers = NodeAnswers(node_id="n", kind="workstation", assignments=[], listen=[], token="x", join="")
    raw = tomllib.loads(render_config(answers, tmp_path))
    assert raw["node"]["assignments"] == []
    assert raw["node"]["listen"] == []
    assert raw["swarm"]["join"] == ""


def test_systemd_unit(tmp_path: Path):
    config = tmp_path / "config.toml"
    text = render_systemd_unit("/opt/bin/sa-home-node", config, tmp_path / "work")
    lines = text.splitlines()
    assert f"ExecStart=/opt/bin/sa-home-node run --config {config}" in lines
    assert f"WorkingDirectory={tmp_path / 'work'}" in lines
    assert "Restart=on-failure" in lines
    assert "WantedBy=default.target" in lines
    assert any(line.startswith("Environment=PATH=") for line in lines)
    assert not any("telegram" in line for line in lines)
    assert text.endswith("\n")
