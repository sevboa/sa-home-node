from pathlib import Path

import pytest
from pydantic import BaseModel

from sa_home_node.config import DEFAULT_CONFIG, ConfigError, NodeConfig, NodeSection, load_config


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
