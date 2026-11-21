from pathlib import Path

import pytest
from pydantic import BaseModel

from sa_home_node.config import DEFAULT_CONFIG, ConfigError, NodeConfig, NodeSection, load_config
from sa_home_node.swarm.config import DiscoverySection, SwarmSection, swarm_section


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(text)
    return p


def test_default_path() -> None:
    assert DEFAULT_CONFIG == Path("~/.config/sa-home-bot/config.toml")


def test_read(tmp_path: Path) -> None:
    p = write(
        tmp_path,
        '[node]\nid = "n1"\ndata_dir = "store"\n\n[extra]\nkey = "v"\n',
    )
    cfg = load_config(p)
    assert cfg.node == NodeSection(id="n1", data_dir=Path("store"))
    assert cfg.sections == {"extra": {"key": "v"}}
    assert cfg.path == p


@pytest.mark.parametrize(
    "text",
    ["", '[node]\nid = "  "\n', '[other]\nx = 1\n', '[node]\nplugins = 1\n'],
)
def test_bad_config(tmp_path: Path, text: str) -> None:
    p = write(tmp_path, text)
    with pytest.raises(ConfigError, match=str(p)):
        load_config(p)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config.toml"):
        load_config(tmp_path / "config.toml")


def test_relative_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    p = write(tmp_path, '[node]\nid = "n"\ndata_dir = "d"\n')
    cfg = load_config(p)
    assert cfg.data_dir == work / "d"
    assert cfg.content_dir == tmp_path / "content"


def test_absolute_paths(tmp_path: Path) -> None:
    p = write(tmp_path, '[node]\nid = "n"\ndata_dir = "/x"\ncontent_dir = "/y"\n')
    cfg = load_config(p)
    assert cfg.data_dir == Path("/x")
    assert cfg.content_dir == Path("/y")


def test_section(tmp_path: Path) -> None:
    p = write(tmp_path, '[node]\nid = "n"\n\n[svc]\nport = 1\n')
    cfg = load_config(p)

    class SvcM(BaseModel):
        port: int = 0

    assert cfg.section("svc", SvcM).port == 1
    assert cfg.section("none", SvcM).port == 0


def test_section_error(tmp_path: Path) -> None:
    p = write(tmp_path, '[node]\nid = "n"\n\n[svc]\nport = "x"\n')

    class SvcM(BaseModel):
        port: int

    cfg = NodeConfig(path=p, node=NodeSection(id="n"), sections={"svc": {"port": "x"}})
    with pytest.raises(ConfigError, match="svc"):
        cfg.section("svc", SvcM)
    with pytest.raises(ConfigError, match="svc"):
        load_config(p).section("svc", SvcM)


def test_extra_keys(tmp_path: Path) -> None:
    p = write(
        tmp_path,
        '[node]\nid = "n"\nunknown_key = ["svc"]\npower_rule = "x"\nidle_poweroff = true\n'
        "\n[monitor]\nport = 1\n",
    )
    cfg = load_config(p)
    assert cfg.node == NodeSection(id="n")
    assert cfg.sections == {"monitor": {"port": 1}}


@pytest.mark.parametrize(
    ("listen", "expected"),
    [
        ('"tcp://192.0.2.10:8710"', ["tcp://192.0.2.10:8710"]),
        ('["tcp://192.0.2.10:8710"]', ["tcp://192.0.2.10:8710"]),
        ('""', []),
    ],
)
def test_listen(tmp_path: Path, listen: str, expected: list[str]) -> None:
    p = write(tmp_path, f'[node]\nid = "n"\nlisten = {listen}\n')
    assert load_config(p).node.listen == expected


def test_socket_state_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    p = write(
        tmp_path,
        '[node]\nid = "n"\nsocket = "./data/node.sock"\nstate_path = "./data/node-state.json"\n',
    )
    cfg = load_config(p)
    assert cfg.socket_path == work / "data" / "node.sock"
    assert cfg.state_file == work / "data" / "node-state.json"


def test_socket_state_absolute(tmp_path: Path) -> None:
    p = write(tmp_path, '[node]\nid = "n"\nsocket = "/s"\nstate_path = "/st"\n')
    cfg = load_config(p)
    assert cfg.socket_path == Path("/s")
    assert cfg.state_file == Path("/st")


def test_swarm_section(tmp_path: Path) -> None:
    p = write(
        tmp_path,
        '[node]\nid = "n"\n\n[swarm]\ntoken = "test-token"\nunknown_key = 7.0\n'
        "\n[swarm.discovery]\nport = 4001\n",
    )
    cfg = load_config(p)
    s = swarm_section(cfg)
    assert s == SwarmSection(
        token="test-token",
        discovery=DiscoverySection(port=4001),
    )
    assert s.node_down_alert_after_s == 300.0


def test_supervisor_keys_defaults(tmp_path: Path) -> None:
    p = write(tmp_path, '[node]\nid = "n1"\n')
    cfg = load_config(p)
    assert cfg.node.assignments == []
    assert cfg.node.restart_delay_s == 5.0
    assert cfg.node.stop_timeout_s == 90.0
    assert cfg.node.power_controllable is None
    s = swarm_section(cfg)
    assert s.failover_grace_s == 120.0
    assert s.superior_ready_timeout_s == 45.0


def test_supervisor_keys_read(tmp_path: Path) -> None:
    p = write(
        tmp_path,
        '[node]\nid = "n"\nassignments = ["svc", "svc-i@one:standby"]\nrestart_delay_s = 3.0\n'
        'stop_timeout_s = 30.0\npower_controllable = false\nidle_poweroff = true\n'
        '\n[node.power_rule]\nsvc_service = "x"\n\n[swarm]\nfailover_grace_s = 60.0\n'
        'superior_ready_timeout_s = 25.0\n',
    )
    cfg = load_config(p)
    assert cfg.node.assignments == ["svc", "svc-i@one:standby"]
    assert cfg.node.restart_delay_s == 3.0
    assert cfg.node.stop_timeout_s == 30.0
    assert cfg.node.power_controllable is False
    s = swarm_section(cfg)
    assert s.failover_grace_s == 60.0
    assert s.superior_ready_timeout_s == 25.0


@pytest.mark.parametrize(
    "field",
    ["restart_delay_s", "stop_timeout_s"],
)
def test_supervisor_keys_positive(tmp_path: Path, field: str) -> None:
    p = write(tmp_path, f'[node]\nid = "n"\n{field} = 0\n')
    with pytest.raises(ConfigError):
        load_config(p)


@pytest.mark.parametrize(
    "field",
    ["node_down_alert_after_s", "failover_grace_s", "superior_ready_timeout_s"],
)
def test_supervisor_keys_positive_swarm(tmp_path: Path, field: str) -> None:
    p = write(tmp_path, f'[node]\nid = "n"\n\n[swarm]\n{field} = 0\n')
    with pytest.raises(ConfigError):
        swarm_section(load_config(p))


def test_bad_kind(tmp_path: Path) -> None:
    p = write(tmp_path, '[node]\nid = "n"\nkind = "alien"\n')
    with pytest.raises(ConfigError, match="node"):
        load_config(p)
